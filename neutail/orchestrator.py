"""Lead Orchestrator (§02/§03) — routes, holds session context, self-checks.

The confidence check on intent classification is the reflection pattern
from §03 made real: below CONFIDENCE_THRESHOLD, it returns a clarifying
question instead of committing to a specialist blind (Fig. 01/02's
self-loop on this box).

classify_intent() calls the fast-tier model to actually understand the
message, with the original keyword matcher as its deterministic fallback
— same live/fallback shape as gateway.call_model everywhere else in this
codebase, and the same "don't trust live=True, trust whether the parsed
result actually got used" fix from muse.py's _rank_products applies here
too: a successful-but-unparseable classification falls back honestly.
"""

import json

from neutail import gateway, session_store
from neutail.agents import care, concierge, muse, persona, tailor

CONFIDENCE_THRESHOLD = 0.5
VALID_INTENTS = {"discovery", "fit", "service", "unknown"}

_INTENT_KEYWORDS = {
    "discovery": ["date night", "something for", "show me", "outfit", "find me"],
    "fit": ["size", "fit", "runs small", "in my size"],
    "service": ["styling question", "style advice", "help me style", "service channel"],
}

CLASSIFY_SYSTEM_PROMPT = (
    "You are an intent router for a retail customer chat. Classify the customer's message "
    "into exactly one of: 'discovery' (wants product recommendations, e.g. \"show me something "
    "for date night\", \"find me an outfit\"), 'fit' (asking about sizing or fit, e.g. \"does this "
    "run small\", \"what size should I get\"), 'service' (wants to talk to a stylist or has a "
    "styling question), or 'unknown' (greetings, small talk, or anything that isn't clearly one "
    "of the above). Give a genuinely calibrated confidence — use below 0.5 whenever the message "
    "is ambiguous or doesn't clearly fit one category, not only when it's plainly 'unknown'. "
    "Reply with ONLY JSON: {\"intent\": \"discovery\"|\"fit\"|\"service\"|\"unknown\", "
    "\"confidence\": <float 0-1>}."
)


def _classify_intent_keywords(message: str) -> tuple[str, float]:
    q = message.lower()
    matched = [intent for intent, kws in _INTENT_KEYWORDS.items() if any(kw in q for kw in kws)]
    if len(matched) == 1:
        return matched[0], 0.9
    if len(matched) > 1:
        return matched[0], 0.6
    return "unknown", 0.2


def _keyword_fallback_json(message: str) -> str:
    intent, confidence = _classify_intent_keywords(message)
    return json.dumps({"intent": intent, "confidence": confidence})


def classify_intent(message: str) -> tuple[str, float, bool]:
    """Returns (intent, confidence, used_live_model)."""
    text, live = gateway.call_model(
        "lead_orchestrator", "fast", CLASSIFY_SYSTEM_PROMPT, message,
        fallback=lambda: _keyword_fallback_json(message),
    )
    try:
        parsed = json.loads(gateway.extract_json(text))
        intent = parsed["intent"]
        confidence = float(parsed["confidence"])
        if intent not in VALID_INTENTS or not (0.0 <= confidence <= 1.0):
            raise ValueError(f"unusable classification: {parsed}")
        return intent, confidence, live
    except Exception:
        intent, confidence = _classify_intent_keywords(message)
        return intent, confidence, False


def _resolve_ordinal_reference(message: str, session_id: str) -> tuple[str | None, str | None]:
    last_results = session_store.load(session_id, "last_results") or []
    if not last_results:
        return None, None
    q = message.lower()
    idx = 1 if "second" in q else 2 if "third" in q else 0
    if idx >= len(last_results):
        idx = 0
    sku = last_results[idx]
    category_map = session_store.load(session_id, "last_category_by_sku") or {}
    return sku, category_map.get(sku)


def handle_message(session_id: str, customer_id: str, message: str) -> dict:
    intent, confidence, intent_live_model = classify_intent(message)

    # self-check: reflection (Fig. 01/02) — don't route blind below threshold.
    # "unknown" always clarifies regardless of confidence: unlike the old
    # keyword matcher, a live model can be *highly* confident a message is
    # unclear (e.g. "hello" scores ~0.95 unknown) — that's a well-calibrated
    # classification, not a reason to skip straight to the unhandled
    # "unknown" response type instead of asking what they meant.
    if intent == "unknown" or confidence < CONFIDENCE_THRESHOLD:
        return {
            "type": "clarify",
            "intent_guess": intent,
            "confidence": confidence,
            "intent_live_model": intent_live_model,
            "message": "Could you say a bit more? I want to route this to the right specialist "
                       f"(best guess: {intent}).",
        }

    if intent == "discovery":
        segment = persona.run(customer_id)["segment"]
        ranked = muse.run(customer_id, segment, message, top_n=4)
        session_store.save(session_id, "segment", segment)
        session_store.save(session_id, "last_results", [r["sku"] for r in ranked["results"]])
        session_store.save(session_id, "last_category_by_sku", {r["sku"]: r["category"] for r in ranked["results"]})
        return {
            "type": "discovery",
            "intent": intent,
            "confidence": confidence,
            "intent_live_model": intent_live_model,
            "segment": segment,
            "used_live_model": ranked["used_live_model"],
            "results": ranked["results"],
        }

    if intent == "fit":
        sku, category = _resolve_ordinal_reference(message, session_id)
        if category is None:
            return {"type": "error", "message": "no product in context to size — ask discovery first"}
        fit = tailor.run(customer_id, category)
        return {
            "type": "fit", "intent": intent, "confidence": confidence, "intent_live_model": intent_live_model,
            "sku": sku, "category": category, **fit,
        }

    if intent == "service":
        contact = care.run(customer_id)
        if not contact["upsell_flag"]:
            return {
                "type": "service", "intent": intent, "confidence": confidence, "intent_live_model": intent_live_model,
                "upsell": False, "contact": contact,
            }
        offer = concierge.run(customer_id, amount=60.0, plan="standard")
        return {
            "type": "service", "intent": intent, "confidence": confidence, "intent_live_model": intent_live_model,
            "upsell": True, "contact": contact, **offer,
        }

    return {"type": "unknown", "intent": intent, "confidence": confidence, "intent_live_model": intent_live_model}
