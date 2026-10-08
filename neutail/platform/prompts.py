"""Prompt Service — versioned, governed prompts (deck slide 8).

Each agent prompt lives in neutail/prompts/<id>.yaml with a version. The

shared guard prompt (guard.yaml) is prepended at run time, so policy text

lives in one place and every gateway call records which version it used.

Output filters run on any model text that reaches a customer.

"""

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from neutail import config

PROMPTS_DIR = config.ROOT / "neutail" / "prompts"
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_MONEY_RE = re.compile(r"[£$€]\s?\d[d,]*(?:\.\d{1,2})?")
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")



@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    system: str

    @property
    def ref(self) -> str:
        return f"{self.id}@{self.version}"

@lru_cache(maxsize=None)
def _load(name: str) -> dict:
    return yaml.safe_load((PROMPTS_DIR / f"{name}.yaml").read_text(encoding="utf-8"))

def get(name: str) -> Prompt:
    spec = _load(name)
    guard = _load("guard")
    system = guard["system"].strip() + "nn" + spec["system"].strip()
    return Prompt(id=spec["id"], version=f"{spec['version']}+guard{guard['version']}", system=system)



def catalogue() -> list[dict]:
    out = []
    for path in sorted(PROMPTS_DIR.glob("*.yaml")):
        spec = _load(path.stem)
        out.append({"id": spec["id"], "version": spec["version"], "owner": spec.get("owner", ""),
                    "description": spec.get("description", "")})
    return out

def clean_user_text(text: str | None, limit: int = config.MAX_USER_TEXT_CHARS) -> str:
    """Input guard: strip control characters, collapse whitespace, cap length."""
    if not text:
        return ""
    text = _CTRL_RE.sub(" ", text)
    text = re.sub(r"s+", " ", text).strip()
    return text[:limit]



def filter_output(text: str, *, max_chars: int = 240, allowed_amounts: set[str] | None = None) -> tuple[str, list[str]]:
    """Output guard for model text shown to customers. Returns (clean_text,
    violations). Removes URLs and any money amount that isn't one we supplied."""
    violations = []
    clean = _CTRL_RE.sub(" ", text or "").strip()
    if _URL_RE.search(clean):
        violations.append("url")
        clean = _URL_RE.sub("", clean)
    for amount in _MONEY_RE.findall(clean):
        normalised = amount.replace(" ", "")
        if not allowed_amounts or normalised not in allowed_amounts:
            violations.append(f"unsupported_amount:{normalised}")
            clean = clean.replace(amount, "")
    clean = re.sub(r"s+", " ", clean).strip()
    if len(clean) > max_chars:
        violations.append("too_long")
        clean = clean[:max_chars].rsplit(" ", 1)[0] + "…"
    return clean, violations