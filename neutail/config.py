"""Process-wide settings. Every value can be overridden by an environment
variable (a `.env` file works — see neutail/__init__.py); the defaults are
demo-safe, not production-safe, and say so where it matters."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("NEUTAIL_DB_PATH", ROOT / "data" / "neutail.db"))
CHROMA_PATH = Path(os.environ.get("NEUTAIL_CHROMA_PATH", ROOT / "data" / "chroma"))
ASSETS_DIR = ROOT / "assets"

# GBP to match the deck (UK estate, 820K loyalty members).
CURRENCY = "GBP"
CURRENCY_SYMBOL = "£"


def money(amount: float) -> str:
    return f"{CURRENCY_SYMBOL}{amount:,.2f}"


# --- auth -------------------------------------------------------------------
# Demo defaults so the stack runs with zero setup. Override all four in any
# shared environment — anyone holding a staff key acts as that principal.
TOKEN_SECRET = os.environ.get("NEUTAIL_TOKEN_SECRET", "dev-only-token-secret-change-me")
TOKEN_TTL_SECONDS = int(os.environ.get("NEUTAIL_TOKEN_TTL_SECONDS", str(8 * 3600)))
STAFF_KEYS = {
    os.environ.get("NEUTAIL_REVIEWER_KEY", "dev-reviewer-key"): ("human_reviewer", "reviewer"),
    os.environ.get("NEUTAIL_ADMIN_KEY", "dev-admin-key"): ("admin", "admin"),
    os.environ.get("NEUTAIL_MCP_KEY", "dev-mcp-key"): ("external_mcp", "external"),
}

# --- memory -----------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(30 * 60)))
REDIS_RETRY_SECONDS = int(os.environ.get("NEUTAIL_REDIS_RETRY_SECONDS", "30"))

# --- guardrails -------------------------------------------------------------
RATE_LIMIT_PER_MINUTE = int(os.environ.get("NEUTAIL_RATE_LIMIT_PER_MINUTE", "120"))
SIDE_EFFECT_RATE_LIMIT_PER_MINUTE = int(os.environ.get("NEUTAIL_SIDE_EFFECT_RATE_LIMIT_PER_MINUTE", "20"))
MAX_USER_TEXT_CHARS = 500

# --- models -----------------------------------------------------------------
MODELS_FILE = ROOT / "config" / "models.json"


def load_models() -> dict:
    """capability -> {provider: model_id}. One map for every provider so the
    same capability never silently resolves to different model generations
    depending on which API key happens to be set. NEUTAIL_MODEL_<CAPABILITY>_<PROVIDER>
    overrides a single entry, e.g. NEUTAIL_MODEL_FAST_OPENROUTER."""
    models = json.loads(MODELS_FILE.read_text())["capabilities"]
    for capability, providers in models.items():
        for provider in list(providers):
            override = os.environ.get(f"NEUTAIL_MODEL_{capability.upper()}_{provider.upper()}")
            if override:
                providers[provider] = override
    return models
