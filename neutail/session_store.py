"""Session memory store (§07's short-term tier only — durable memory stays
exactly where it was, in the customer/loyalty/fit-profile tables).

Same live/fallback shape as gateway.call_model: Redis with a TTL when
reachable, else the original SQLite `session_context` table, so the demo
still runs with zero external services if Redis isn't up. Availability is
checked once and cached — if Redis comes up after this process started,
restart it to pick that up (a demo-scale tradeoff, not a production one).
"""

import json
import os

from neutail.db import get_connection

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", str(30 * 60)))  # 30 min of inactivity

_client = None
_checked = False


def _redis():
    global _client, _checked
    if _checked:
        return _client
    _checked = True
    try:
        import redis

        candidate = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5)
        candidate.ping()
        _client = candidate
    except Exception:
        _client = None
    return _client


def backend() -> str:
    """Which store is actually serving reads/writes right now — surfaced on
    the UI so it's visible which mode the demo is running in."""
    return "redis" if _redis() is not None else "sqlite"


def save(session_id: str, key: str, value) -> None:
    payload = json.dumps(value)
    client = _redis()
    if client is not None:
        try:
            client.set(f"session:{session_id}:{key}", payload, ex=SESSION_TTL_SECONDS)
            return
        except Exception:
            pass  # fall through to SQLite rather than lose the write
    _save_sqlite(session_id, key, payload)


def load(session_id: str, key: str):
    client = _redis()
    if client is not None:
        try:
            raw = client.get(f"session:{session_id}:{key}")
            return json.loads(raw) if raw is not None else None
        except Exception:
            pass
    return _load_sqlite(session_id, key)


def _save_sqlite(session_id: str, key: str, payload: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            """INSERT INTO session_context (session_id, key, value, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(session_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (session_id, key, payload),
        )
    conn.close()


def _load_sqlite(session_id: str, key: str):
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM session_context WHERE session_id = ? AND key = ?", (session_id, key)
    ).fetchone()
    conn.close()
    return json.loads(row["value"]) if row else None
