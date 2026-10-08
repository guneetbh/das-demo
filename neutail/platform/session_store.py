"""Short-term (session) memory — the context object between turns.

Redis with a TTL when reachable, else the SQLite `session_context` table.
Keys are scoped by customer *and* session, so reusing a session_id with a
different customer can never hand over someone else's results. A failed Redis
check is retried after NEUTAIL_REDIS_RETRY_SECONDS instead of latching
"unavailable" for the life of the process.
"""

import json
import threading
import time

from neutail import config, db

_client = None
_next_check = 0.0
_lock = threading.Lock()


def _redis():
    global _client, _next_check
    with _lock:
        if _client is not None or time.monotonic() < _next_check:
            return _client
        try:
            import redis

            candidate = redis.Redis.from_url(config.REDIS_URL, socket_connect_timeout=0.3, socket_timeout=0.5)
            candidate.ping()
            _client = candidate
        except Exception:
            _client = None
            _next_check = time.monotonic() + config.REDIS_RETRY_SECONDS
        return _client


def _drop_redis() -> None:
    global _client, _next_check
    with _lock:
        _client = None
        _next_check = time.monotonic() + config.REDIS_RETRY_SECONDS


def backend() -> str:
    return "redis" if _redis() is not None else "sqlite"


def _key(customer_id: str, session_id: str) -> str:
    return f"session:{customer_id}:{session_id}"


def save(customer_id: str, session_id: str, value: dict) -> None:
    payload = json.dumps(value, default=str)
    client = _redis()
    if client is not None:
        try:
            client.set(_key(customer_id, session_id), payload, ex=config.SESSION_TTL_SECONDS)
            return
        except Exception:
            _drop_redis()
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO session_context (customer_id, session_id, value, updated_at)
               VALUES (?, ?, ?, datetime('now'))
               ON CONFLICT(customer_id, session_id) DO UPDATE SET value = excluded.value,
                                                               updated_at = excluded.updated_at""",
            (customer_id, session_id, payload),
        )
    finally:
        conn.close()


def load(customer_id: str, session_id: str) -> dict | None:
    client = _redis()
    if client is not None:
        try:
            raw = client.get(_key(customer_id, session_id))
            return json.loads(raw) if raw is not None else None
        except Exception:
            _drop_redis()
    conn = db.get_connection()
    try:
        row = conn.execute(
            """SELECT value FROM session_context WHERE customer_id = ? AND session_id = ?
                 AND updated_at >= datetime('now', ?)""",
            (customer_id, session_id, f"-{config.SESSION_TTL_SECONDS} seconds"),
        ).fetchone()
    finally:
        conn.close()
    return json.loads(row["value"]) if row else None
