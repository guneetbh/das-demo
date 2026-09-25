"""Model Gateway (Fig. 01/03/04 — routing · fallback · prompts · tokens · logs).

Routes by tier (reasoning/fast/vision, per §05's model-routing policy).
Tries OpenRouter first (if OPENROUTER_API_KEY is set), then a direct
Anthropic call (if ANTHROPIC_API_KEY is set), then every caller's
deterministic `fallback` — so the demo runs on seeded data alone with
neither configured, and either key just makes MUSE/CONCIERGE's reasoning
live instead of canned. OpenRouter goes through `requests` (already a
hard dependency, unlike `anthropic`) against its OpenAI-compatible
chat/completions endpoint rather than a dedicated SDK.
"""

import os
import re
from typing import Callable

import requests

from neutail.db import get_connection

MODEL_POOL = {
    "reasoning": "claude-opus-5",
    "fast": "claude-haiku-4-5-20251001",
}

# OpenRouter's catalog uses its own provider-prefixed slugs, not Anthropic's
# own model IDs — best-effort mapping; a wrong slug surfaces as a real,
# logged error (see call_model) rather than a silent wrong-model call.
OPENROUTER_MODEL_POOL = {
    "reasoning": "anthropic/claude-opus-4.1",
    "fast": "anthropic/claude-haiku-4.5",
}
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL)


def extract_json(text: str) -> str:
    """Strips a markdown code fence around a JSON payload, if present.
    Every caller expecting JSON back from a live model should parse
    through this rather than calling json.loads(text) directly — a
    "reply with ONLY JSON" system prompt doesn't reliably stop a model
    from wrapping its answer in ```json ... ``` anyway (confirmed: Haiku
    did this on a real call classifying intent, even instructed not to).
    A no-op on the deterministic fallback's own json.dumps() output,
    which never has fences, so it's safe to apply unconditionally."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    return match.group(1).strip() if match else stripped


def _call_openrouter(api_key: str, tier: str, system: str, prompt: str) -> str:
    resp = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": OPENROUTER_MODEL_POOL[tier],
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "max_tokens": 4096,
        },
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()["choices"][0]["message"]["content"]


def _call_anthropic(api_key: str, tier: str, system: str, prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL_POOL[tier],
        system=system,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=4096,  # 1024 truncated MUSE's JSON mid-response at real candidate-list sizes
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def call_model(agent: str, tier: str, system: str, prompt: str, fallback: Callable[[], str]) -> tuple[str, bool]:
    """Returns (text, used_live_model). Tries OpenRouter, then direct
    Anthropic, then falls back — each attempt's real failure is logged
    (see _log), not swallowed, so a wrong model slug or a billing issue
    shows up as an audit_log row instead of a silent identical fallback."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    if openrouter_key:
        try:
            text = _call_openrouter(openrouter_key, tier, system, prompt)
            _log(agent, tier, live=True, detail="live=True (openrouter)")
            return text, True
        except Exception as exc:
            _log(agent, tier, live=False, detail=f"openrouter call failed: {type(exc).__name__}: {exc}")

    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    if anthropic_key:
        try:
            text = _call_anthropic(anthropic_key, tier, system, prompt)
            _log(agent, tier, live=True, detail="live=True (anthropic direct)")
            return text, True
        except Exception as exc:
            _log(agent, tier, live=False, detail=f"anthropic call failed: {type(exc).__name__}: {exc}")

    if not openrouter_key and not anthropic_key:
        _log(agent, tier, live=False, detail="no OPENROUTER_API_KEY or ANTHROPIC_API_KEY set")
    return fallback(), False


def _log(agent: str, tier: str, live: bool, detail: str | None = None) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO audit_log (caller, tool, allowed, detail) VALUES (?, ?, 1, ?)",
            (agent, f"model_gateway:{tier}", detail or f"live={live}"),
        )
    conn.close()
