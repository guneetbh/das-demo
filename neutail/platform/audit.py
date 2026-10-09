"""The single audit writer — the evals & observability plane.

Every tool call, model call, retrieval and human decision lands here with the
request's trace_id, so one `/chat` turn can be followed end to end
(`GET /admin/traces/{trace_id}`). `detail` is redacted before it is written:
customer IDs are hashed, contact fields are dropped, long lists become counts.
"""

import hashlib
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from neutail import db

current_trace: ContextVar[str | None] = ContextVar("neutail_trace", default=None)
current_actor: ContextVar[str | None] = ContextVar("neutail_actor", default=None)

_DROP_KEYS = {"email", "name", "masked_contact", "city", "offer_text", "description", "persona_blurb"}
_HASH_KEYS = {"customer_id"}
_MAX_STR = 160
_MAX_LIST = 5


def hash_customer(customer_id: str | None) -> str | None:
    if not customer_id:
        return customer_id
    return "cust#" + hashlib.sha256(customer_id.encode()).hexdigest()[:10]


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key in _DROP_KEYS:
                out[key] = "[redacted]"
            elif key in _HASH_KEYS and isinstance(item, str):
                out[key] = hash_customer(item)
            else:
                out[key] = redact(item, depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_LIST:
            return {"count": len(value), "head": [redact(v, depth + 1) for v in value[:2]]}
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str) and len(value) > _MAX_STR:
        return value[:_MAX_STR] + "…"
    return value


def record(
    *,
    kind: str,
    caller: str,
    tool: str,
    outcome: str = "ok",
    allowed: bool = True,
    detail: Any = None,
    latency_ms: int | None = None,
    model: str | None = None,
    prompt_version: str | None = None,
    actor: str | None = None,
) -> None:
    row = (
        current_trace.get(),
        kind,
        caller,
        actor or current_actor.get(),
        tool,
        int(allowed),
        outcome,
        latency_ms,
        model,
        prompt_version,
        json.dumps(redact(detail), default=str) if detail is not None else None,
    )

    def write() -> None:
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT INTO audit_log (trace_id, kind, caller, actor, tool, allowed, outcome,
                                          latency_ms, model, prompt_version, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
        finally:
            conn.close()

    # Never inside the business transaction: it would contend for the write
    # lock, and a rollback would erase the record of the failure.
    db.after_transaction(write)


class Span:
    def __init__(self) -> None:
        self.outcome = "ok"
        self.detail: dict = {}
        self.model: str | None = None
        self.prompt_version: str | None = None


@contextmanager
def span(*, kind: str, caller: str, tool: str, detail: dict | None = None) -> Iterator[Span]:
    """Times a call and records it exactly once — ok, or error with the exception."""
    s = Span()
    s.detail = dict(detail or {})
    started = time.perf_counter()
    try:
        yield s
    except Exception as exc:
        s.outcome = s.outcome if s.outcome not in ("ok",) else "error"
        s.detail["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record(
            kind=kind, caller=caller, tool=tool, outcome=s.outcome, allowed=True, detail=s.detail,
            latency_ms=int((time.perf_counter() - started) * 1000), model=s.model,
            prompt_version=s.prompt_version,
        )


def recent(limit: int = 50, trace_id: str | None = None) -> list[dict]:
    with db.connection() as conn:
        if trace_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE trace_id = ? ORDER BY log_id", (trace_id,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
