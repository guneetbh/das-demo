"""Lead Orchestrator (§02/§03) — routes, holds session context, self-checks.

The confidence check on intent classification is the reflection pattern
from §03 made real: below CONFIDENCE_THRESHOLD, it returns a clarifying
question instead of committing to a specialist blind (Fig. 01/02's
self-loop on this box).

classify_intents() calls the fast-tier model to actually understand the
message, with the original keyword matcher as its deterministic fallback
— same live/fallback shape as gateway.call_model everywhere else in this
codebase, and the same "don't trust live=True, trust whether the parsed
result actually got used" fix from muse.py's _rank_products applies here
too: a successful-but-unparseable classification falls back honestly.

Multi-intent: classify_intents() returns a *list* of (intent, confidence)
pairs, not one — almost always length 1 (the entire single-intent
behavior below is unchanged for that case), but a compound message like
"show me something for date night, and check my fit for jeans" can
legitimately classify as two. handle_message() dispatches to each
qualifying intent's existing handler independently, then
response_composer.compose() merges their results into one reply — see
that module for why merging isn't this module's job.

Async: every agent call below goes through mcp_client.call_tool() now
(real MCP, in-process — see neutail/mcp_client.py), which is async, so
this whole module is. gateway.py itself stays fully synchronous on
purpose (it's also called from inside synchronous tool handlers, e.g.
muse.py's _rank_products, which can't become async — see mcp_server.py's
docstring) — classify_intents() reaches it via asyncio.to_thread() so a
live model call (15-40s) never blocks this process's shared event loop,
the same reasoning session_store's calls below are wrapped for too.
"""

import asyncio
import json
import re

from neutail import gateway, response_composer, session_store
from neutail.agents import care, concierge, muse, persona, tailor

CONFIDENCE_THRESHOLD = 0.5
VALID_INTENTS = {"discovery", "fit", "service", "unknown"}

_INTENT_KEYWORDS = {
    "discovery": ["date night", "something for", "show me", "outfit", "find me"],
    "fit": ["size", "fit", "runs small", "in my size"],
    "service": ["styling question", "style advice", "help me style", "service channel"],
}

CLASSIFY_SYSTEM_PROMPT = (
    "You are an intent router for a retail customer chat. The input is JSON: "
    "{\"message\": str, \"has_recent_results\": bool}. has_recent_results is true when the "
    "customer was already shown a list of products earlier this session — in that case, a short "
    "follow-up like \"the second one, in my size\" or \"that one, bigger\" almost certainly means "
    "'fit' (they're referencing an item from that list), not 'unknown', even though you can't see "
    "the list itself. Classify the message into a JSON array of one or more objects, each "
    "{\"intent\": ..., \"confidence\": <float 0-1>} — one entry per DISTINCT, independently-"
    "satisfiable request in the message. Almost every real message has exactly one entry. Only "
    "return more than one when the message clearly asks for multiple separate things, typically "
    "joined by 'and' (e.g. \"show me something for date night, and check my fit for jeans\" is "
    "'discovery' + 'fit') — never split a single request into artificial parts just because it's "
    "phrased with multiple clauses. Each intent is one of: 'discovery' (wants product "
    "recommendations, e.g. \"show me something for date night\", \"find me an outfit\"), 'fit' "
    "(asking about sizing or fit, including short follow-ups referencing a prior list per above), "
    "'service' (wants to talk to a stylist or has a styling question), or 'unknown' (greetings, "
    "small talk, or anything that isn't clearly one of the above). Give a genuinely calibrated "
    "confidence per entry — below 0.5 whenever that part is ambiguous or doesn't clearly fit one "
    "category, not only when it's plainly 'unknown'. Reply with ONLY the JSON array, e.g. "
    "[{\"intent\": \"discovery\", \"confidence\": 0.92}]."
)


def _classify_intents_keywords(message: str) -> list[tuple[str, float]]:
    """All matched categories, not just the first. Before multi-intent existed,
    two distinct keyword hits in one message was treated as ambiguity about a
    single intent (return the first match, at a knocked-down 0.6 confidence);
    now it's read as genuine multi-intent evidence instead — two independent
    signals is a *stronger* claim than one, not a weaker one, so each keeps
    its own confidence (0.75) rather than being deduped to a single guess.
    A single match (0.9) or zero matches ("unknown", 0.2) are unchanged from
    before this was multi-label — the only real behavior change is what used
    to be the len(matched) > 1 case."""
    q = message.lower()
    matched = [intent for intent, kws in _INTENT_KEYWORDS.items() if any(kw in q for kw in kws)]
    if not matched:
        return [("unknown", 0.2)]
    if len(matched) == 1:
        return [(matched[0], 0.9)]
    return [(intent, 0.75) for intent in matched]


def _keyword_fallback_json(message: str) -> str:
    return json.dumps([{"intent": i, "confidence": c} for i, c in _classify_intents_keywords(message)])


async def classify_intents(message: str, has_recent_results: bool = False) -> tuple[list[tuple[str, float]], bool]:
    """Returns (intents, used_live_model) — intents is a list of (intent,
    confidence) pairs, almost always length 1. has_recent_results tells the
    live classifier whether there's a discovery list in this session to
    refer back to — without it, a short follow-up like "the second one, in
    my size" reads as ambiguous 'unknown' in isolation (confirmed: scored
    unknown/0.3 live, where the old keyword matcher's blunt "size" substring
    match got it right by accident). The keyword fallback doesn't need this
    signal — it already matches on "size" regardless of context."""
    prompt = json.dumps({"message": message, "has_recent_results": has_recent_results})
    text, live = await asyncio.to_thread(
        gateway.call_model,
        "lead_orchestrator", "fast", CLASSIFY_SYSTEM_PROMPT, prompt,
        fallback=lambda: _keyword_fallback_json(message),
    )
    try:
        parsed = json.loads(gateway.extract_json(text))
        if not isinstance(parsed, list) or not parsed:
            raise ValueError(f"unusable classification: {parsed}")
        intents = []
        for item in parsed:
            intent = item["intent"]
            confidence = float(item["confidence"])
            if intent not in VALID_INTENTS or not (0.0 <= confidence <= 1.0):
                raise ValueError(f"unusable classification entry: {item}")
            intents.append((intent, confidence))
        return intents, live
    except Exception:
        return _classify_intents_keywords(message), False


_ORDINAL_WORDS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth"]
# Require an ordinal suffix or a # — a bare number would collide with "size 8"
# in the exact same message ("the second one, in my size 8"), so this never
# matches on size alone.
_ORDINAL_SUFFIX_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b")
_ORDINAL_HASH_RE = re.compile(r"#(\d+)\b")


async def _resolve_ordinal_reference(message: str, session_id: str) -> tuple[str | None, str | None]:
    """Only covered "second"/"third" before — silently wrong (defaulted to
    the first item) for anything past that, including "the fourth one" even
    though discovery shows exactly 4 results by default. Now covers ordinal
    words up to eighth plus "4th"/"#4" forms, with headroom past today's
    top_n=4 rather than a limit tied to it."""
    last_results = await asyncio.to_thread(session_store.load, session_id, "last_results") or []
    if not last_results:
        return None, None
    q = message.lower()

    idx = None
    for i, word in enumerate(_ORDINAL_WORDS):
        if word in q:
            idx = i
            break
    if idx is None:
        match = _ORDINAL_SUFFIX_RE.search(q) or _ORDINAL_HASH_RE.search(q)
        if match:
            idx = int(match.group(1)) - 1
    if idx is None:
        idx = 0  # no explicit reference — "the one we're already looking at"

    if idx < 0 or idx >= len(last_results):
        idx = 0
    sku = last_results[idx]
    category_map = await asyncio.to_thread(session_store.load, session_id, "last_category_by_sku") or {}
    return sku, category_map.get(sku)


async def _handle_discovery(customer_id: str, message: str, session_id: str,
                             intent: str, confidence: float, intent_live_model: bool) -> dict:
    persona_result = await persona.run(customer_id)
    segment = persona_result["segment"]
    ranked = await muse.run(customer_id, segment, message, top_n=4)
    await asyncio.to_thread(session_store.save, session_id, "segment", segment)
    await asyncio.to_thread(session_store.save, session_id, "last_results", [r["sku"] for r in ranked["results"]])
    await asyncio.to_thread(
        session_store.save, session_id, "last_category_by_sku",
        {r["sku"]: r["category"] for r in ranked["results"]},
    )
    return {
        "type": "discovery",
        "intent": intent,
        "confidence": confidence,
        "intent_live_model": intent_live_model,
        "segment": segment,
        "used_live_model": ranked["used_live_model"],
        "results": ranked["results"],
    }


async def _handle_fit(customer_id: str, message: str, session_id: str,
                       intent: str, confidence: float, intent_live_model: bool) -> dict:
    sku, category = await _resolve_ordinal_reference(message, session_id)
    if category is None:
        return {"type": "error", "message": "no product in context to size — ask discovery first"}
    fit = await tailor.run(customer_id, category)
    return {
        "type": "fit", "intent": intent, "confidence": confidence, "intent_live_model": intent_live_model,
        "sku": sku, "category": category, **fit,
    }


async def _handle_service(customer_id: str, message: str, session_id: str,
                           intent: str, confidence: float, intent_live_model: bool) -> dict:
    contact = await care.run(customer_id)
    if not contact["upsell_flag"]:
        return {
            "type": "service", "intent": intent, "confidence": confidence, "intent_live_model": intent_live_model,
            "upsell": False, "contact": contact,
        }
    offer = await concierge.run(customer_id, amount=60.0, plan="standard")
    return {
        "type": "service", "intent": intent, "confidence": confidence, "intent_live_model": intent_live_model,
        "upsell": True, "contact": contact, **offer,
    }


# Verbatim extractions of what used to be handle_message()'s if/elif branches —
# same signature, same returns, so the single-intent path below is byte-for-byte
# unchanged from before multi-intent existed. Keyed by intent name so both the
# single-dispatch and the composite-dispatch paths can share one lookup.
_HANDLERS = {"discovery": _handle_discovery, "fit": _handle_fit, "service": _handle_service}


async def handle_message(session_id: str, customer_id: str, message: str) -> dict:
    has_recent_results = bool(await asyncio.to_thread(session_store.load, session_id, "last_results"))
    intents, intent_live_model = await classify_intents(message, has_recent_results)

    primary_intent, primary_confidence = intents[0]

    # self-check: reflection (Fig. 01/02) — don't route blind below threshold.
    # "unknown" always clarifies regardless of confidence: unlike the old
    # keyword matcher, a live model can be *highly* confident a message is
    # unclear (e.g. "hello" scores ~0.95 unknown) — that's a well-calibrated
    # classification, not a reason to skip straight to the unhandled
    # "unknown" response type instead of asking what they meant. Gating on
    # just the first classified intent, before ever looking at the rest of
    # the list, matches exactly what this check did before multi-intent
    # existed for every message that classifies as a single intent.
    if primary_intent == "unknown" or primary_confidence < CONFIDENCE_THRESHOLD:
        return {
            "type": "clarify",
            "intent_guess": primary_intent,
            "confidence": primary_confidence,
            "intent_live_model": intent_live_model,
            "message": "Could you say a bit more? I want to route this to the right specialist "
                       f"(best guess: {primary_intent}).",
        }

    # Every entry that's a real, above-threshold, handleable intent — always
    # includes at least primary_intent, since it just passed the gate above
    # and "discovery"/"fit"/"service" are the only non-"unknown" members of
    # VALID_INTENTS, so this list is never empty here.
    qualifying = [(i, c) for i, c in intents if i in _HANDLERS and c >= CONFIDENCE_THRESHOLD]

    if len(qualifying) == 1:
        intent, confidence = qualifying[0]
        return await _HANDLERS[intent](customer_id, message, session_id, intent, confidence, intent_live_model)

    # Multi-intent: each handler runs independently — same call each would
    # make on its own, no awareness of the others — then response_composer
    # merges the results. This is the only new control-flow path; every
    # single-intent message above still returns from the branch above,
    # untouched. Sequential await, deliberately not asyncio.gather: the fit
    # handler's emergent behavior (resolving its category against the
    # discovery handler's just-written session-memory results) depends on
    # discovery genuinely finishing first — gathering concurrently would
    # race that (see README.md's "Multi-intent classification" section).
    parts = []
    for intent, confidence in qualifying:
        parts.append(await _HANDLERS[intent](customer_id, message, session_id, intent, confidence, intent_live_model))
    return response_composer.compose(parts, intent_live_model)
