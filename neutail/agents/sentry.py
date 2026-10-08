"""SENTRY — checkout risk & approval; the human-in-the-loop gate.

Scores each charge rather than comparing it to one number. Approves below

the threshold; otherwise writes a pending escalation (idempotently) that a

human resolves. It never commits anything itself.

"""

from pydantic import BaseModel, Field

from neutail.context import Context

from neutail.data import crm, escalations, transactional

from neutail.platform import contracts

AGENT_NAME = "sentry_agent"

AMOUNT_THRESHOLDS = {"subscription": 75.0, "order": 250.0}

ESCALATE_AT = 0.5


class PaymentCheckIn(BaseModel):

    amount: float = Field(gt=0, le=10000)

    kind: str = Field(pattern="^(subscription|order)$")

    sku: str | None = Field(default=None, max_length=32)

    size: str | None = Field(default=None, max_length=8)

    quantity: int = Field(default=1, ge=1, le=10)

    plan: str | None = Field(default=None, max_length=32)

    offer_id: str | None = Field(default=None, max_length=64)

    idempotency_key: str | None = Field(default=None, max_length=128)


def score(customer: dict, stats: dict, amount: float, kind: str) -> tuple[float, list[str]]:

    reasons, risk = [], 0.0

    threshold = AMOUNT_THRESHOLDS[kind]

    if amount > threshold:

        risk += 0.5

        reasons.append(f"£{amount:.2f} is over the £{threshold:.2f} auto-approve bound for a {kind}")

    if stats["n_orders"] == 0:

        risk += 0.3

        reasons.append("first-time payer")

    elif stats["avg_amount"] and amount > 3 * stats["avg_amount"]:

        risk += 0.25

        reasons.append(f"{amount / stats['avg_amount']:.1f}× this customer's average order")

    if stats["n_last_24h"] >= 3:

        risk += 0.25

        reasons.append(f"{stats['n_last_24h']} charges in the last 24h")

    if customer["tenure_months"] < 3:

        risk += 0.15

        reasons.append("account under 3 months old")

    return round(min(risk, 1.0), 2), reasons


def _check_payment_policy(ctx: Context, amount: float, kind: str, sku: str | None, size: str | None,

                          quantity: int, plan: str | None, offer_id: str | None, idempotency_key: str | None) -> dict:

    if idempotency_key:

        existing = escalations.by_idempotency_key(idempotency_key)

        if existing:

            return {"approved": False, "escalated": True, "escalation_id": existing["escalation_id"],

                    "risk_score": existing["risk_score"], "reason": existing["reason"], "replayed": True}

    customer = crm.require_customer(ctx.customer_id)

    stats = transactional.stats(ctx.customer_id)

    risk, reasons = score(customer, stats, amount, kind)

    if risk < ESCALATE_AT:

        return {"approved": True, "escalated": False, "risk_score": risk, "signals": reasons}

    reason = "; ".join(reasons)

    escalation_id = escalations.insert(

        customer_id=ctx.customer_id, kind=kind, amount=amount, reason=reason, risk_score=risk, sku=sku,

        size=size, quantity=quantity, plan=plan, offer_id=offer_id, idempotency_key=idempotency_key,

    )

    return {"approved": False, "escalated": True, "escalation_id": escalation_id, "risk_score": risk, "reason": reason}


contracts.register(

    "check_payment_policy", owner=AGENT_NAME, allowed_callers=["concierge_agent"],

    handler=_check_payment_policy, input_model=PaymentCheckIn,

    domains={"crm": "R", "transactional": "R", "escalations": "RW"}, side_effect=True,

)