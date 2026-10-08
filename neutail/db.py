"""SQLite connection handling and the unit of work.

`transaction()` opens one write transaction (BEGIN IMMEDIATE) and publishes
its connection through a ContextVar; every data-layer call made inside it —
including calls from nested tool invocations on the same thread — joins that
transaction instead of opening its own. That is what makes "write the order,
award the points, close the escalation" commit or roll back as one unit.

Work registered with `after_transaction()` (the audit writer uses it) runs
once the outermost transaction has committed or rolled back, so audit rows
are never lost to a rollback and never contend for the write lock.
"""

import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable, Iterator

from neutail import config

SCHEMA_PATH = config.ROOT / "neutail" / "schema.sql"

_active: ContextVar[sqlite3.Connection | None] = ContextVar("neutail_tx", default=None)
_deferred: ContextVar[list[Callable[[], None]] | None] = ContextVar("neutail_tx_deferred", default=None)


def get_connection() -> sqlite3.Connection:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """The active transaction's connection if there is one, else a short-lived
    autocommit connection."""
    active = _active.get()
    if active is not None:
        yield active
        return
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def in_transaction() -> bool:
    return _active.get() is not None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    if _active.get() is not None:  # nested: join the outer unit of work
        yield _active.get()
        return
    conn = get_connection()
    deferred: list[Callable[[], None]] = []
    tx_token = _active.set(conn)
    deferred_token = _deferred.set(deferred)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        _active.reset(tx_token)
        _deferred.reset(deferred_token)
        conn.close()
        for fn in deferred:
            fn()


def after_transaction(fn: Callable[[], None]) -> None:
    """Run `fn` after the current transaction ends, or now if there isn't one."""
    deferred = _deferred.get()
    if deferred is None:
        fn()
    else:
        deferred.append(fn)


def init_schema(reset: bool = False) -> None:
    conn = get_connection()
    try:
        if reset:
            # Drop tables rather than deleting the file: a running API may hold it open.
            conn.execute("PRAGMA foreign_keys = OFF")
            tables = [r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
            for table in tables:
                conn.execute(f'DROP TABLE IF EXISTS "{table}"')
            conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(SCHEMA_PATH.read_text())
    finally:
        conn.close()
