"""ResponseComposer — merges several per-intent results into one reply for a
multi-intent message. A plain module, not an agent: it touches no data and
calls no model, so there is nothing to policy-gate. Each agent still returns
to the orchestrator; the orchestrator calls compose() once and replies.
"""

_LABELS = {
    "discovery": "some picks for you",
    "fit": "fit guidance",
    "service": "a styling offer",
    "loyalty": "your loyalty summary",
    "subscription": "your subscription",
    "clarify": "a quick question",
}


def _label(part: dict) -> str:
    return _LABELS.get(part.get("type"), part.get("type", "a result"))

def compose(parts: list[dict]) -> dict:
    labels = [_label(p) for p in parts]
    summary = "Here's " + (labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + f", and {labels[-1]}") + "."
    return {"type": "composite", "parts": parts, "summary": summary}