"""Human Review Queue (§03/§04) — not a separate agent or service, per
Fig. 03: it's the `escalations` table, and this is the resolve path a
human reviewer (via the API) writes back through. Approving a pending
escalation completes the commit SENTRY paused — for either kind:
'subscription' (no sku, per-customer) or 'order' (a specific product,
found a real gap: this only ever handled 'subscription' until commit_order
existed and this file was never updated for it — approving an order
escalation silently did nothing but flip its status, no transactional
row, no points, "nothing happened" from the reviewer's side).
"""

import uuid

from neutail import runtime
from neutail.agents import tally  # noqa: F401 — registers earn_points
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
        if approve and row["kind"] in ("subscription", "order"):
            prefix = "SUB" if row["kind"] == "subscription" else "ORD"
            order_id = f"{prefix}-{row['customer_id']}-{uuid.uuid4().hex[:10]}"
            conn.execute(
                """INSERT INTO transactional (order_id, customer_id, sku, kind, amount)
                   VALUES (?, ?, ?, ?, ?)""",
                (order_id, row["customer_id"], row["sku"], row["kind"], row["amount"]),
            )
    conn.close()

    points = None
    if order_id is not None:
        # the order landed, same as CONCIERGE's direct-approval path — TALLY earns points either way
        points = tally.run(row["customer_id"], row["amount"], source=row["kind"])

    runtime.log_event(
        resolved_by, "resolve_escalation", allowed=True,
        detail=f"escalation_id={escalation_id} status={new_status} order_id={order_id}",
    )
    result = {"escalation_id": escalation_id, "status": new_status, "order_id": order_id}
    if points is not None:
        result["points_earned"] = points["points_earned"]
        result["points_balance"] = points["points_balance"]
    return result
