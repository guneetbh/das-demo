"""Human Review Queue (§03/§04) — not a separate agent or service, per
Fig. 03: it's the `escalations` table, and this is the resolve path a
human reviewer (via the API) writes back through. Approving a pending
subscription escalation completes the commit SENTRY paused.
"""

import time

from neutail import runtime
from neutail.db import get_connection

REVIEWER_CALLER = "human_reviewer"


def list_escalations(status: str | None = "pending") -> list[dict]:
    conn = get_connection()
    if status:
        rows = conn.execute(
            "SELECT * FROM escalations WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM escalations ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(row) for row in rows]


def resolve_escalation(escalation_id: int, approve: bool, resolved_by: str = REVIEWER_CALLER) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM escalations WHERE escalation_id = ?", (escalation_id,)
    ).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"no such escalation: {escalation_id}")
    if row["status"] != "pending":
        conn.close()
        raise ValueError(f"escalation {escalation_id} is already {row['status']}")

    new_status = "approved" if approve else "denied"
    order_id = None
    with conn:
        conn.execute(
            """UPDATE escalations SET status = ?, resolved_at = datetime('now'), resolved_by = ?
               WHERE escalation_id = ?""",
            (new_status, resolved_by, escalation_id),
        )
        if approve and row["kind"] == "subscription":
            order_id = f"SUB-{row['customer_id']}-{int(time.time())}"
            conn.execute(
                """INSERT INTO transactional (order_id, customer_id, sku, kind, amount)
                   VALUES (?, ?, NULL, 'subscription', ?)""",
                (order_id, row["customer_id"], row["amount"]),
            )
    conn.close()

    runtime.log_event(
        resolved_by, "resolve_escalation", allowed=True,
        detail=f"escalation_id={escalation_id} status={new_status} order_id={order_id}",
    )
    return {"escalation_id": escalation_id, "status": new_status, "order_id": order_id}
