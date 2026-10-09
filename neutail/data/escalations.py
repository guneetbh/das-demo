import sqlite3

from neutail import db
from neutail.data import access


@access("escalations", "R")
def get(escalation_id: int) -> dict | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM escalations WHERE escalation_id = ?", (escalation_id,)).fetchone()
    return dict(row) if row else None


@access("escalations", "R")
def by_idempotency_key(key: str) -> dict | None:
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM escalations WHERE idempotency_key = ?", (key,)).fetchone()
    return dict(row) if row else None


@access("escalations", "R")
def listing(status: str | None = "pending") -> list[dict]:
    with db.connection() as conn:
        if status:
            rows = conn.execute(
                """SELECT e.*, c.name AS product_name, c.image_url FROM escalations e
                   LEFT JOIN catalogue c ON c.sku = e.sku WHERE e.status = ? ORDER BY e.created_at DESC""",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT e.*, c.name AS product_name, c.image_url FROM escalations e
                   LEFT JOIN catalogue c ON c.sku = e.sku ORDER BY e.created_at DESC"""
            ).fetchall()
    return [dict(r) for r in rows]


@access("escalations", "W")
def insert(*, customer_id: str, kind: str, amount: float, reason: str, risk_score: float,
           sku: str | None = None, size: str | None = None, quantity: int = 1, plan: str | None = None,
           offer_id: str | None = None, idempotency_key: str | None = None) -> int:
    with db.transaction() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO escalations (customer_id, kind, amount, sku, size, quantity, plan, offer_id,
                                            risk_score, reason, idempotency_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (customer_id, kind, amount, sku, size, quantity, plan, offer_id, risk_score, reason, idempotency_key),
            )
            return cur.lastrowid
        except sqlite3.IntegrityError:
            existing = conn.execute(
                "SELECT escalation_id FROM escalations WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is None:
                raise
            return existing["escalation_id"]


@access("escalations", "W")
def resolve(escalation_id: int, status: str, resolved_by: str, order_id: str | None = None) -> bool:
    """Compare-and-set on status='pending' — the second of two concurrent
    reviewers gets False instead of a duplicate order."""
    with db.transaction() as conn:
        cur = conn.execute(
            """UPDATE escalations SET status = ?, resolved_by = ?, resolved_at = datetime('now'), order_id = ?
               WHERE escalation_id = ? AND status = 'pending'""",
            (status, resolved_by, order_id, escalation_id),
        )
    return cur.rowcount == 1
