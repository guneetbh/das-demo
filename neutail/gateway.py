"""Model Gateway (Fig. 01/03/04 — routing · fallback · prompts · tokens · logs).

Routes by tier (reasoning/fast/vision, per §05's model-routing policy).
If ANTHROPIC_API_KEY isn't set, or the call fails, every caller supplies
a deterministic `fallback` — so the demo runs on seeded data alone, and
a real key just makes MUSE/CONCIERGE's reasoning live instead of canned.
"""

import os
from typing import Callable

from neutail.db import get_connection

MODEL_POOL = {
    "reasoning": "claude-opus-5",
    "fast": "claude-haiku-4-5-20251001",
}


def call_model(agent: str, tier: str, system: str, prompt: str, fallback: Callable[[], str]) -> tuple[str, bool]:
    """Returns (text, used_live_model)."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=api_key)
            resp = client.messages.create(
                model=MODEL_POOL[tier],
                system=system,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=4096,  # 1024 truncated MUSE's JSON mid-response at real candidate-list sizes
            )
            text = "".join(block.text for block in resp.content if block.type == "text")
            _log(agent, tier, live=True)
            return text, True
        except Exception as exc:
            # Swallowing this was hiding real failures (rate limits, timeouts,
            # truncation) behind an identical-looking fallback — logged now,
            # not printed, so it shows up in the audit trail without risking
            # the key itself ending up anywhere (SDK exceptions don't include
            # it, but nothing here echoes request headers regardless).
            _log(agent, tier, live=False, detail=f"live call failed: {type(exc).__name__}: {exc}")
            return fallback(), False

    _log(agent, tier, live=False, detail="no ANTHROPIC_API_KEY set")
    return fallback(), False


def _log(agent: str, tier: str, live: bool, detail: str | None = None) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO audit_log (caller, tool, allowed, detail) VALUES (?, ?, 1, ?)",
            (agent, f"model_gateway:{tier}", detail or f"live={live}"),
        )
    conn.close()
