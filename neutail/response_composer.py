"""ResponseComposer — merges independent per-intent results into one
client-facing reply, for the (rare) case orchestrator.handle_message()
dispatches to more than one specialist in a single turn — e.g. "show me
something for date night, and check my fit for jeans" classifies as both
'discovery' and 'fit' above CONFIDENCE_THRESHOLD.

Deliberately NOT an agent: no tool contract, no policy check, no
audit-log entry of its own. Every agent in this system either reads/
writes the database or calls a model — composing an already-computed
list of dicts touches neither, so there's nothing here worth
policy-gating, the same reasoning that keeps gateway.py, session_store.py
and vector_store.py as plain importable modules rather than agents.

Placement matters: each specialist still returns to the Orchestrator
directly, exactly as it does for a single-intent message — nothing here
receives an agent's result before the Orchestrator does. The Orchestrator
collects every qualifying intent's result itself, then makes one call out
to compose() and back, then replies to the client. A ResponseComposer
that instead sat between the agents and the Orchestrator (agents calling
it directly) would have no precedent anywhere else in this codebase,
where every agent always replies to whoever called it.
"""

_INTENT_LABELS = {
    "discovery": "some product recommendations",
    "fit": "fit guidance",
    "service": "a styling offer",
}


def _label_for(part: dict) -> str:
    return _INTENT_LABELS.get(part.get("intent"), part.get("type", "a result"))


def _summarize(parts: list[dict]) -> str:
    labels = [_label_for(p) for p in parts]
    if len(labels) == 1:
        return f"Here's {labels[0]}."
    return "Here's " + ", ".join(labels[:-1]) + f", and {labels[-1]}."


def compose(parts: list[dict], intent_live_model: bool) -> dict:
    """parts is the list of per-intent result dicts handle_message()'s
    existing _handle_* functions already produce — same shape each already
    had on its own, just plural. Nothing here re-derives or duplicates what
    any specialist already computed; it only decides how to present several
    of them together."""
    return {
        "type": "composite",
        "intent_live_model": intent_live_model,
        "intents": [p.get("intent", p.get("type")) for p in parts],
        "parts": parts,
        "summary": _summarize(parts),
    }
