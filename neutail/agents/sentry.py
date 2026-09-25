"""SENTRY — UC4 payment policy (§04: provides check_payment_policy).

This is the human-in-the-loop pattern from §03 made real: SENTRY doesn't
have a special escalation wire (Fig. 03's point) — it just writes a row
to `escalations` through the same tool path as everything else, and
CONCIERGE reads that status back instead of treating "not approved" as
a denial.
"""

from neutail import contracts, runtime
from neutail.db import get_connection

AGENT_NAME = "sentry_agent"

FIRST_TIME_PAYER_TENURE_MONTHS = 3
# A recurring subscription and a one-time product order carry different
# risk: $75/month compounds, a single purchase doesn't, so a threshold
# sized for subscriptions would send nearly every premium product
# ($140-280 in the seeded catalogue) to human review regardless of the
# customer. Kept per-kind rather than one shared number.
AMOUNT_THRESHOLDS = {"subscription": 75.0, "order": 300.0}
DEFAULT_AMOUNT_THRESHOLD = 75.0


def _check_payment_policy(customer_id: str, amount: float, kind: str = "subscription") -> dict:
    conn = get_connection()
    customer = conn.execute(
        "SELECT tenure_months FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if customer is None:
        conn.close()
        raise ValueError(f"unknown customer_id: {customer_id}")

    threshold = AMOUNT_THRESHOLDS.get(kind, DEFAULT_AMOUNT_THRESHOLD)
    first_time_payer = customer["tenure_months"] < FIRST_TIME_PAYER_TENURE_MONTHS
    over_amount = amount > threshold

    if not (first_time_payer or over_amount):
        conn.close()
        return {"approved": True, "escalated": False}

    reason = ", ".join(
        r for r, cond in [
            ("first-time payer", first_time_payer),
            (f"amount ${amount:.2f} over ${threshold:.2f} policy bound for {kind}", over_amount),
        ] if cond
    )
    with conn:
        cursor = conn.execute(
            """INSERT INTO escalations (customer_id, kind, amount, reason, status)
               VALUES (?, ?, ?, ?, 'pending')""",
            (customer_id, kind, amount, reason),
        )
        escalation_id = cursor.lastrowid
    conn.close()
    return {
        "approved": False,
        "escalated": True,
        "escalation_id": escalation_id,
        "reason": reason,
    }


contracts.register(
    "check_payment_policy",
    allowed_callers=["sentry_agent", "lead_orchestrator", "concierge_agent"],
    handler=_check_payment_policy,
)


def run(customer_id: str, amount: float, kind: str = "subscription") -> dict:
    """SENTRY's entry point — the Orchestrator/CONCIERGE calls this (Fig. 02, step 6)."""
    return runtime.invoke_tool(
        AGENT_NAME, "check_payment_policy", customer_id=customer_id, amount=amount, kind=kind
    )
