"""Every test session runs against a fresh, seeded, throwaway database with
live model calls disabled, so results are deterministic."""

import os
import sys
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="neutail-test-"))
os.environ["NEUTAIL_DB_PATH"] = str(_tmp / "test.db")
os.environ["NEUTAIL_CHROMA_PATH"] = str(_tmp / "chroma")
os.environ["REDIS_URL"] = "redis://127.0.0.1:1/0"  # unreachable -> SQLite session memory
for key in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY"):
    os.environ[key] = ""
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from neutail import agents, seed  # noqa: E402,F401
from neutail.orchestration import orchestrator  # noqa: E402
from neutail.context import Context  # noqa: E402
from neutail.platform import audit, policy  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def seeded():
    seed.seed()


@pytest.fixture(autouse=True)
def _reset_limits():
    policy.reset_rate_limits()


def shopper_ctx(customer_id: str, session_id: str = "t", principal: str = "storefront") -> Context:
    ctx = Context(principal=principal, customer_id=customer_id, session_id=session_id,
                  actor=f"shopper:{audit.hash_customer(customer_id)}", role="shopper")
    audit.current_trace.set(ctx.trace_id)
    return ctx


def orchestrator_ctx(customer_id: str, session_id: str = "t") -> Context:
    return shopper_ctx(customer_id, session_id, principal=orchestrator.ORCHESTRATOR)


def staff_ctx(principal: str, role: str) -> Context:
    ctx = Context(principal=principal, actor=principal, role=role)
    audit.current_trace.set(ctx.trace_id)
    return ctx
