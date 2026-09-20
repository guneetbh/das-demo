"""Lead Orchestrator (§02/§03) — routes, holds session context, self-checks.

The confidence check on intent classification is the reflection pattern
from §03 made real: below CONFIDENCE_THRESHOLD, it returns a clarifying
question instead of committing to a specialist blind (Fig. 01/02's
self-loop on this box).
"""

from neutail import session_store
from neutail.agents import care, concierge, muse, persona, tailor

CONFIDENCE_THRESHOLD = 0.5

_INTENT_KEYWORDS = {
    "discovery": ["date night", "something for", "show me", "outfit", "find me"],
    "fit": ["size", "fit", "runs small", "in my size"],
    "service": ["styling question", "style advice", "help me style", "service channel"],
}


def classify_intent(message: str) -> tuple[str, float]:
    q = message.lower()
    matched = [intent for intent, kws in _INTENT_KEYWORDS.items() if any(kw in q for kw in kws)]
    if len(matched) == 1:
        return matched[0], 0.9
    if len(matched) > 1:
        return matched[0], 0.6
    return "unknown", 0.2


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
    intent, confidence = classify_intent(message)

    # self-check: reflection (Fig. 01/02) — don't route blind below threshold
    if confidence < CONFIDENCE_THRESHOLD:
        return {
            "type": "clarify",
            "intent_guess": intent,
            "confidence": confidence,
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
            "segment": segment,
            "used_live_model": ranked["used_live_model"],
            "results": ranked["results"],
        }

    if intent == "fit":
        sku, category = _resolve_ordinal_reference(message, session_id)
        if category is None:
            return {"type": "error", "message": "no product in context to size — ask discovery first"}
        fit = tailor.run(customer_id, category)
        return {"type": "fit", "intent": intent, "confidence": confidence, "sku": sku, "category": category, **fit}

    if intent == "service":
        contact = care.run(customer_id)
        if not contact["upsell_flag"]:
            return {"type": "service", "intent": intent, "confidence": confidence, "upsell": False, "contact": contact}
        offer = concierge.run(customer_id, amount=60.0, plan="standard")
        return {"type": "service", "intent": intent, "confidence": confidence, "upsell": True, "contact": contact, **offer}

    return {"type": "unknown", "intent": intent, "confidence": confidence}
