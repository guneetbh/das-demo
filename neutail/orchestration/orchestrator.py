"""Lead Orchestrator — route, decompose, hold context, self-check.

One turn:
  1. load the context object from session memory (customer-scoped)
  2. classify intents + entities; below the confidence gate, ask instead of guessing
  3. publish PERSONA's segment onto the context once
  4. dispatch each qualifying intent in dependency order (discovery before fit,
     so "show me X and check my fit" can size what it just showed)
  5. compose, save the context, reply

The orchestrator holds no agent logic: every agent call is runtime.invoke(),
made as `lead_orchestrator` on behalf of the bound customer.
"""

from neutail import agents  # noqa: F401 — registers every contract
from neutail.orchestration import intents, response_composer
from neutail.context import Context
from neutail.platform import prompts, runtime, session_store

ORCHESTRATOR = "lead_orchestrator"
DISPATCH_ORDER = {"discovery": 0, "fit": 1, "loyalty": 2, "service": 3, "accept_offer": 4}


def _as_orchestrator(ctx: Context) -> Context:
    return ctx.as_caller(ORCHESTRATOR)


def load_context(ctx: Context) -> Context:
    return ctx.restore(session_store.load(ctx.customer_id, ctx.session_id))


def save_context(ctx: Context) -> None:
    session_store.save(ctx.customer_id, ctx.session_id, ctx.memory())


def _publish_segment(ctx: Context) -> dict:
    ctx.segment = runtime.invoke(_as_orchestrator(ctx), "get_customer_segment")
    return ctx.segment


# --------------------------------------------------------------------------- handlers
def _discovery(ctx: Context, message: str, intent: intents.Intent) -> dict:
    ranked = runtime.invoke(_as_orchestrator(ctx), "rank_products", query=message, top_n=8, source="search",
                            category=intent.category)
    ctx.last_results = [{"sku": r["sku"], "category": r["category"], "name": r["name"]} for r in ranked["results"]]
    return {"type": "discovery", "intent": intent.intent, "confidence": intent.confidence,
            "category": intent.category, **ranked}


def _resolve_fit_target(ctx: Context, message: str, intent: intents.Intent) -> tuple[str | None, str | None]:
    sku = intents.extract_sku(message)
    if sku:
        return sku, None
    if intent.category:
        # Named category wins; if we just showed one, size that exact item.
        match = next((r for r in ctx.last_results if r["category"] == intent.category), None)
        return (match["sku"] if match else None), intent.category
    if ctx.last_results:
        idx = (intent.ordinal or 1)
        idx = len(ctx.last_results) if idx == -1 else idx
        if not 1 <= idx <= len(ctx.last_results):
            idx = 1
        picked = ctx.last_results[idx - 1]
        return picked["sku"], picked["category"]
    return None, None


def _fit(ctx: Context, message: str, intent: intents.Intent) -> dict:
    sku, category = _resolve_fit_target(ctx, message, intent)
    if sku is None and category is None:
        return {"type": "clarify", "intent": "fit", "confidence": intent.confidence,
                "message": "Happy to help with fit — which item or category? e.g. “how do your jeans fit me?”"}
    fit = runtime.invoke(_as_orchestrator(ctx), "get_fit_profile", category=category, sku=sku, log_event=True)
    return {"type": "fit", "intent": intent.intent, "confidence": intent.confidence, **fit}


def _service(ctx: Context, message: str, intent: intents.Intent) -> dict:
    offer = runtime.invoke(_as_orchestrator(ctx), "compose_offer")
    ctx.pending_offer = {k: offer[k] for k in ("offer_id", "plan", "amount")} if offer.get("offered") else None
    return {"type": "service", "intent": intent.intent, "confidence": intent.confidence, **offer}


def _loyalty(ctx: Context, message: str, intent: intents.Intent) -> dict:
    status = runtime.invoke(_as_orchestrator(ctx), "get_loyalty_status")
    challenges = runtime.invoke(_as_orchestrator(ctx), "get_challenges")
    return {"type": "loyalty", "intent": intent.intent, "confidence": intent.confidence, **status,
            "badges": challenges["badges"]}


def _accept(ctx: Context, message: str, intent: intents.Intent) -> dict:
    if not ctx.pending_offer:
        return {"type": "clarify", "intent": "accept_offer", "confidence": intent.confidence,
                "message": "There's no open offer to accept right now — ask about styling to see one."}
    offer_id = ctx.pending_offer["offer_id"]
    result = runtime.invoke(_as_orchestrator(ctx), "accept_offer", offer_id=offer_id,
                            idempotency_key=f"chat:{ctx.session_id}:{offer_id}")
    ctx.pending_offer = None
    return {"type": "subscription", "intent": intent.intent, "confidence": intent.confidence, **result}


HANDLERS = {"discovery": _discovery, "fit": _fit, "service": _service, "loyalty": _loyalty, "accept_offer": _accept}


# --------------------------------------------------------------------------- entry points
def handle_message(ctx: Context, message: str) -> dict:
    load_context(ctx)
    message = prompts.clean_user_text(message)
    found, intent_live = intents.classify(message, bool(ctx.last_results), bool(ctx.pending_offer))
    primary = found[0]

    if primary.intent == "unknown" or primary.confidence < intents.CONFIDENCE_THRESHOLD:
        save_context(ctx)
        return {"type": "clarify", "intent_guess": primary.intent, "confidence": primary.confidence,
                "intent_live_model": intent_live, "trace_id": ctx.trace_id,
                "message": "I can find pieces for an occasion, check your fit, connect you with a stylist, or "
                           "look at your points. What would you like?"}

    qualifying = sorted((i for i in found if i.intent in HANDLERS and i.confidence >= intents.CONFIDENCE_THRESHOLD),
                        key=lambda i: DISPATCH_ORDER[i.intent])
    _publish_segment(ctx)
    parts = [HANDLERS[i.intent](ctx, message, i) for i in qualifying]
    save_context(ctx)

    if len(parts) == 1:
        reply = parts[0]
    else:
        reply = response_composer.compose(parts)
    reply.update({"intent_live_model": intent_live, "trace_id": ctx.trace_id,
                  "segment": ctx.segment["segment"], "micro_segment": ctx.segment.get("micro_segment"),
                  "intents": [i.as_dict() for i in qualifying]})
    return reply


def recommend(ctx: Context, category: str | None = None, similar_to: str | None = None, top_n: int = 8) -> dict:
    """The storefront's 'Recommended for you' / 'You might also like' rows —
    same MUSE pipeline, deterministic ranking (no model latency on page load),
    logged as impressions."""
    load_context(ctx)
    _publish_segment(ctx)
    ranked = runtime.invoke(_as_orchestrator(ctx), "rank_products", query="", top_n=top_n,
                            source="similar" if similar_to else "recommendation", category=category,
                            exclude_sku=similar_to)
    save_context(ctx)
    return {**ranked, "trace_id": ctx.trace_id, "segment": ctx.segment["segment"],
            "micro_segment": ctx.segment.get("micro_segment"),
            "first_name": ctx.segment.get("first_name"), "preference_tags": ctx.segment.get("preference_tags", [])}
