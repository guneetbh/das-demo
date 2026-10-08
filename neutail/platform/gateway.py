"""Model Gateway — agents ask for a capability (reasoning / fast / vision),

the gateway maps it to a model through config/models.json, tries the

configured providers in order, and falls back to the caller's deterministic

answer if none succeed. Every attempt is audited with model, prompt version,

latency and outcome.

Provider order: OpenRouter (if OPENROUTER_API_KEY), then Anthropic direct

(if ANTHROPIC_API_KEY), then the fallback. Both providers resolve the same

capability to the same model generation.

"""

import os
import re
import time
from typing import Callable

import requests

from neutail import config
from neutail.platform import audit
from neutail.platform.prompts import Prompt

OPENROUTER_URL = "[https://openrouter.ai/api/v1/chat/completions](https://openrouter.ai/api/v1/chat/completions)"
_FENCE_RE = re.compile(r"^```(?:json)\s*\n?```$", re.DOTALL)

_MODELS = config.load_models()
_anthropic_client = None

def models() -> dict:
      return _MODELS


def extract_json(text: str) -> str:
      """Strip a markdown fence around a JSON payload. Models sometimes add one
      even when told not to; a no-op on fallback output."""
      stripped = text.strip()
      match = _FENCE_RE.match(stripped)
      return match.group(1).strip() if match else stripped

def _client():
      global _anthropic_client
      if _anthropic_client is None:
            import anthropic
            _anthropic_client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"), max_retries=2)
      return _anthropic_client

def _call_openrouter(model: str, spec: dict, system: str, prompt: str) -> str:
      resp = requests.post(
            OPENROUTER_URL,
            headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"},
            json={
                  "model": model,
                  "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                  "max_tokens": spec.get("max_tokens", 4096),
            },
            timeout=90,
      )
      if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
      return resp.json()["choices"][0]["message"]["content"]

def _call_anthropic(model: str, spec: dict, system: str, prompt: str) -> str:
      kwargs = {
            "model": model,
            "max_tokens": spec.get("max_tokens", 4096),
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
      }

      if spec.get("effort"):
            kwargs["output_config"] = {"effort": spec["effort"]}

      if model.startswith("claude-opus-5") or model.startswith("claude-sonnet-5-5") or model.startswith("claude-fable"):
            # Server-side refusal fallback; this SDK version has no typed `fallbacks` field.
            resp = _client().beta.messages.create(
                  betas=["server-side-fallback-2026-07-01"], extra_body={"fallbacks": "default"}, **kwargs
            )
      else:
            resp = _client().messages.create(**kwargs)

      if resp.stop_reason == "refusal":
            raise RuntimeError("model declined the request (stop_reason=refusal)")

      if resp.stop_reason == "max_tokens":
            raise RuntimeError("response truncated at max_tokens")

      return "".join(block.text for block in resp.content if block.type == "text")



def call_model(caller: str, capability: str, prompt_obj: Prompt, user_input: str,
                       fallback: Callable[[], str]) -> tuple[str, bool]:

      """Returns (text, used_live_model)."""
      spec = _MODELS[capability]
      attempts = []
      if os.environ.get("OPENROUTER_API_KEY") and spec.get("openrouter"):
            attempts.append(("openrouter", spec["openrouter"], _call_openrouter))

      if os.environ.get("ANTHROPIC_API_KEY") and spec.get("anthropic"):
            attempts.append(("anthropic", spec["anthropic"], _call_anthropic))

      for provider, model, fn in attempts:
            started = time.perf_counter()
            try:
                  text = fn(model, spec, prompt_obj.system, user_input)
                  audit.record(kind="model", caller=caller, tool=f"model_gateway:{capability}", model=model,
                                      prompt_version=prompt_obj.version, latency_ms=_ms(started),
                                      detail={"provider": provider, "live": True})
                  return text, True
            except Exception as exc:
                  audit.record(kind="model", caller=caller, tool=f"model_gateway:{capability}", outcome="error",
                                      model=model, prompt_version=prompt_obj.version, latency_ms=_ms(started),
                                      detail={"provider": provider, "error": f"{type(exc).__name__}: {exc}"})

      audit.record(kind="model", caller=caller, tool=f"model_gateway:{capability}", outcome="fallback",
                          prompt_version=prompt_obj.version,
                          detail={"live": False, "reason": "no provider succeeded" if attempts else "no API key set"})
      return fallback(), False

def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)