"""CONCIERGE — UC4 upsell (§04: provides commit_subscription).

Requires resolve_contact (CARE), check_payment_policy (SENTRY), and now
earn_points (TALLY) per Fig. 03. When SENTRY escalates instead of
approving, CONCIERGE doesn't treat that as a denial — it reports the
pending review back, same as the demo script's second upsell run
(§06 step 6). Points are only earned once the order actually lands,
so the escalated branch doesn't call TALLY at all.
"""

import uuid

from neutail import contracts, gateway, runtime
from neutail.agents import care, sentry, tally  # noqa: F401 — registers resolve_contact, check_payment_policy, earn_points
from neutail.db import get_connection

AGENT_NAME = "concierge_agent"

SYSTEM_PROMPT = (
    "You are CONCIERGE, composing a one-paragraph styling-subscription upsell offer for "
    "a retail customer. Be warm, specific to their segment, and mention the price. Reply "
    "with ONLY the offer text, no preamble."
)


def _fallback_offer(segment: str, amount: float, plan: str) -> str:
    tone = "an exclusive, curated" if segment == "affluent" else "an easy, budget-friendly"
    return (
        f"Hi! Based on what you love, we'd like to offer you {tone} styling subscription "
        f"({plan}) at ${amount:.2f}/mo — new picks delivered monthly, cancel anytime."
    )


def _commit_subscription(customer_id: str, amount: float, plan: str = "standard") -> dict:
    contact = runtime.invoke_tool(AGENT_NAME, "resolve_contact", customer_id=customer_id)
    if not contact["upsell_flag"]:
        return {"offered": False, "reason": "customer not flagged as upsell-eligible by CARE"}

    conn = get_connection()
    seg_row = conn.execute(
        "SELECT tier FROM loyalty WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    conn.close()
    segment = "affluent" if seg_row and seg_row["tier"] in ("Gold", "Platinum") else "value"

    offer_text, live = gateway.call_model(
        AGENT_NAME, "reasoning", SYSTEM_PROMPT, f"segment={segment} amount={amount} plan={plan}",
        fallback=lambda: _fallback_offer(segment, amount, plan),
    )

    policy_result = runtime.invoke_tool(
        AGENT_NAME, "check_payment_policy", customer_id=customer_id, amount=amount, kind="subscription"
    )

    if not policy_result["approved"]:
        return {
            "offered": True,
            "offer_text": offer_text,
            "used_live_model": live,
            "committed": False,
            "escalated": True,
            "escalation_id": policy_result["escalation_id"],
            "status": "pending human review",
            "reason": policy_result["reason"],
        }

    order_id = f"SUB-{customer_id}-{uuid.uuid4().hex[:10]}"
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO transactional (order_id, customer_id, sku, kind, amount) VALUES (?, ?, NULL, 'subscription', ?)",
            (order_id, customer_id, amount),
        )
    conn.close()

    points = runtime.invoke_tool(AGENT_NAME, "earn_points", customer_id=customer_id, amount=amount, source="subscription")

    return {
        "offered": True,
        "offer_text": offer_text,
        "used_live_model": live,
        "committed": True,
        "escalated": False,
        "order_id": order_id,
        "points_earned": points["points_earned"],
        "points_balance": points["points_balance"],
    }


contracts.register(
    "commit_subscription",
    allowed_callers=["concierge_agent", "lead_orchestrator"],
    handler=_commit_subscription,
)


def run(customer_id: str, amount: float, plan: str = "standard") -> dict:
    """CONCIERGE's entry point — the Orchestrator calls this (Fig. 02, step 6)."""
    return runtime.invoke_tool(AGENT_NAME, "commit_subscription", customer_id=customer_id, amount=amount, plan=plan)
