"""TALLY — UC5 loyalty (provides get_loyalty_status, earn_points).

Was out of scope in the original build (§01/§09 of the architecture doc
picked UC4 Upsell over UC5 Loyalty). Brought in now, narrowly: TALLY owns
the loyalty ledger, CONCIERGE still owns the order — same "Transactional |
CONCIERGE commits" / "Loyalty | ..." split from §04's data-ownership table,
just with a real writer for Loyalty for the first time.

Points vary with the purchase: 1 point per dollar spent, scaled by the
customer's current tier multiplier. Tier itself isn't recomputed here —
that's a deliberate simplification, not an oversight (see README).
"""

from neutail import contracts, runtime
from neutail.db import get_connection

AGENT_NAME = "tally_agent"

TIER_MULTIPLIER = {"Bronze": 1.0, "Silver": 1.25, "Gold": 1.5, "Platinum": 2.0}


def _get_loyalty_status(customer_id: str) -> dict:
    conn = get_connection()
    row = conn.execute(
        "SELECT tier, points_balance, ytd_spend FROM loyalty WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"no loyalty record for customer_id: {customer_id}")
    return {
        "customer_id": customer_id,
        "tier": row["tier"],
        "points_balance": row["points_balance"],
        "ytd_spend": row["ytd_spend"],
        "multiplier": TIER_MULTIPLIER.get(row["tier"], 1.0),
    }


def _earn_points(customer_id: str, amount: float, source: str = "purchase") -> dict:
    conn = get_connection()
    loyalty = conn.execute(
        "SELECT tier, points_balance, ytd_spend FROM loyalty WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if loyalty is None:
        conn.close()
        raise ValueError(f"no loyalty record for customer_id: {customer_id}")

    multiplier = TIER_MULTIPLIER.get(loyalty["tier"], 1.0)
    points_earned = round(amount * multiplier)
    new_balance = loyalty["points_balance"] + points_earned
    new_spend = loyalty["ytd_spend"] + amount

    with conn:
        conn.execute(
            "UPDATE loyalty SET points_balance = ?, ytd_spend = ? WHERE customer_id = ?",
            (new_balance, new_spend, customer_id),
        )
    conn.close()

    return {
        "customer_id": customer_id,
        "tier": loyalty["tier"],
        "multiplier": multiplier,
        "amount": amount,
        "points_earned": points_earned,
        "points_balance": new_balance,
        "source": source,
    }


contracts.register(
    "get_loyalty_status",
    allowed_callers=["tally_agent", "lead_orchestrator", "persona_agent", "concierge_agent", "mcp_client"],
    handler=_get_loyalty_status,
)

contracts.register(
    "earn_points",
    allowed_callers=["tally_agent", "lead_orchestrator", "concierge_agent"],
    handler=_earn_points,
)


def status(customer_id: str) -> dict:
    return runtime.invoke_tool(AGENT_NAME, "get_loyalty_status", customer_id=customer_id)


def run(customer_id: str, amount: float, source: str = "purchase") -> dict:
    """TALLY's entry point — CONCIERGE calls this right after a purchase completes."""
    return runtime.invoke_tool(AGENT_NAME, "earn_points", customer_id=customer_id, amount=amount, source=source)
