"""CONCIERGE — UC4 upsell & monetisation, hosted by CARE; SENTRY approves pay.

Money only moves on an explicit customer action:
  compose_offer   read-only pitch — price from `plans`, never from the caller
  accept_offer    the customer said yes → SENTRY → one transaction: order + points
  commit_order    product purchase → SENTRY → one transaction: stock + order + points
  resolve_escalation  a human reviewer's decision completes (or closes) a paused commit

Every commit takes an idempotency key, so a double-click or retry returns the
original result instead of charging twice.
"""

import json
import uuid

from pydantic import BaseModel, Field

from neutail import db
from neutail.context import Context
from neutail.data import catalogue, escalations, inventory, loyalty, transactional
from neutail.platform import audit, contracts, gateway, prompts, runtime

AGENT_NAME = "concierge_agent"
OFFER_TTL_MINUTES = 30


class ComposeIn(BaseModel):
    plan: str | None = Field(default=None, pattern="^(plus|elite)$")


class AcceptIn(BaseModel):
    offer_id: str = Field(min_length=6, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=128)


class OrderIn(BaseModel):
    sku: str = Field(min_length=3, max_length=32)
    size: str = Field(min_length=1, max_length=8)
    quantity: int = Field(default=1, ge=1, le=10)
    idempotency_key: str = Field(min_length=8, max_length=128)


class ResolveIn(BaseModel):
    escalation_id: int = Field(ge=1)
    approve: bool


def _fallback_pitch(segment: dict, plan: dict) -> str:
    if segment.get("segment") == "affluent":
        return f"{plan['display_name']}: a stylist who knows your taste, curating pieces before anyone else sees them."
    return f"{plan['display_name']}: easy, budget-smart outfit picks chosen for you every month — cancel anytime."


def _points_for(order_id: str, source: str) -> int:
    return (loyalty.ledger_entry(source, order_id) or {}).get("delta", 0)


# --------------------------------------------------------------------------- compose (read-only)
def _compose_offer(ctx: Context, plan: str | None) -> dict:
    contact = runtime.invoke(ctx, "resolve_contact")
    if not contact["upsell_flag"]:
        return {"offered": False, "reason": contact["upsell_blocked_reason"], "tier": contact["tier"]}
    segment = ctx.segment or runtime.invoke(ctx, "get_customer_segment")
    plan_id = plan or ("elite" if contact["tier"] == "Platinum" else "plus")
    plan_row = catalogue.get_plan(plan_id)
    if plan_row is None:
        raise ValueError(f"unknown plan: {plan_id}")

    prompt_obj = prompts.get("concierge_offer")
    payload = json.dumps({"segment": segment.get("segment"), "micro_segment": segment.get("micro_segment"),
                          "plan_name": plan_row["display_name"], "perks": plan_row["perks"],
                          "favourite_categories": segment.get("preference_tags", [])})
    text, live = gateway.call_model(AGENT_NAME, "reasoning", prompt_obj, payload,
                                    fallback=lambda: _fallback_pitch(segment, plan_row))
    pitch, violations = prompts.filter_output(text, max_chars=200)
    if violations:
        audit.record(kind="model", caller=AGENT_NAME, tool="output_filter", outcome="filtered",
                     detail={"violations": violations})
    if not pitch:
        pitch, live = _fallback_pitch(segment, plan_row), False

    offer_id = f"OFF-{uuid.uuid4().hex[:12]}"
    transactional.insert_offer(offer_id=offer_id, customer_id=ctx.customer_id, plan=plan_id,
                               amount=plan_row["monthly_price"], offer_text=pitch, ttl_minutes=OFFER_TTL_MINUTES)
    return {"offered": True, "offer_id": offer_id, "plan": plan_id, "plan_name": plan_row["display_name"],
            "amount": plan_row["monthly_price"], "perks": plan_row["perks"].split("|"), "offer_text": pitch,
            "used_live_model": live, "expires_in_minutes": OFFER_TTL_MINUTES, "committed": False}


# --------------------------------------------------------------------------- accept (the commit)
def _accept_offer(ctx: Context, offer_id: str, idempotency_key: str) -> dict:
    offer = transactional.offer(offer_id)
    if offer is None or offer["customer_id"] != ctx.customer_id:
        raise ValueError("no such offer for this customer")
    base = {"offer_id": offer_id, "plan": offer["plan"], "amount": offer["amount"]}

    if offer["status"] == "accepted":
        order = transactional.get_order(offer["order_id"])
        return {**base, "committed": True, "escalated": False, "order_id": order["order_id"],
                "points_earned": _points_for(order["order_id"], "subscription"),
                "points_balance": loyalty.get(ctx.customer_id)["points_balance"], "replayed": True}
    if offer["status"] == "pending_review":
        return {**base, "committed": False, "escalated": True, "status": "pending human review", "replayed": True,
                "reason": "already waiting for a reviewer"}
    if offer["status"] != "open":
        raise ValueError(f"offer is {offer['status']}")
    if transactional.offer_expired(offer_id):
        transactional.transition_offer(offer_id, "open", "expired")
        raise ValueError("offer has expired — ask for a fresh one")

    check = runtime.invoke(ctx, "check_payment_policy", amount=offer["amount"], kind="subscription",
                           plan=offer["plan"], offer_id=offer_id, idempotency_key=f"{idempotency_key}:sentry")
    if not check["approved"]:
        transactional.transition_offer(offer_id, "open", "pending_review")
        return {**base, "committed": False, "escalated": True, "escalation_id": check["escalation_id"],
                "risk_score": check["risk_score"], "status": "pending human review", "reason": check["reason"]}

    order_id = f"SUB-{uuid.uuid4().hex[:10].upper()}"
    with db.transaction():
        if not transactional.transition_offer(offer_id, "open", "accepted", order_id=order_id):
            raise ValueError("offer was already actioned")
        transactional.insert_order(order_id=order_id, customer_id=ctx.customer_id, kind="subscription",
                                   amount=offer["amount"], plan=offer["plan"], idempotency_key=idempotency_key)
        points = runtime.invoke(ctx, "earn_points", amount=offer["amount"], source="subscription", ref_id=order_id)
    return {**base, "committed": True, "escalated": False, "order_id": order_id, "risk_score": check["risk_score"],
            "points_earned": points["points_earned"], "points_balance": points["points_balance"],
            "tier_upgraded_to": points["tier_upgraded_to"]}


# --------------------------------------------------------------------------- product order
def _commit_order(ctx: Context, sku: str, size: str, quantity: int, idempotency_key: str) -> dict:
    existing = transactional.by_idempotency_key(idempotency_key)
    if existing:
        if existing["customer_id"] != ctx.customer_id:
            raise ValueError("idempotency key already used")
        return {"sku": existing["sku"], "size": existing["size"], "amount": existing["amount"], "committed": True,
                "escalated": False, "order_id": existing["order_id"],
                "points_earned": _points_for(existing["order_id"], "order"),
                "points_balance": loyalty.get(ctx.customer_id)["points_balance"], "replayed": True}

    item = catalogue.require(sku)
    if size not in {s["size"] for s in inventory.sizes(sku)}:
        raise ValueError(f"{sku} doesn't come in size {size}")
    price = runtime.invoke(ctx, "get_price_context", skus=[sku])["prices"][sku]["price"]
    amount = round(price * quantity, 2)
    base = {"sku": sku, "name": item["name"], "size": size, "quantity": quantity, "amount": amount}

    check = runtime.invoke(ctx, "check_payment_policy", amount=amount, kind="order", sku=sku, size=size,
                           quantity=quantity, idempotency_key=f"{idempotency_key}:sentry")
    if not check["approved"]:
        return {**base, "committed": False, "escalated": True, "escalation_id": check["escalation_id"],
                "risk_score": check["risk_score"], "status": "pending human review", "reason": check["reason"]}

    order_id = f"ORD-{uuid.uuid4().hex[:10].upper()}"
    with db.transaction():
        inventory.decrement(sku, size, quantity)
        transactional.insert_order(order_id=order_id, customer_id=ctx.customer_id, kind="order", amount=amount,
                                   sku=sku, size=size, quantity=quantity, idempotency_key=idempotency_key)
        points = runtime.invoke(ctx, "earn_points", amount=amount, source="order", ref_id=order_id)
    return {**base, "committed": True, "escalated": False, "order_id": order_id, "risk_score": check["risk_score"],
            "points_earned": points["points_earned"], "points_balance": points["points_balance"],
            "tier_upgraded_to": points["tier_upgraded_to"]}


# --------------------------------------------------------------------------- human review
def _resolve_escalation(ctx: Context, escalation_id: int, approve: bool) -> dict:
    esc = escalations.get(escalation_id)
    if esc is None:
        raise ValueError(f"no such escalation: {escalation_id}")
    if esc["status"] != "pending":
        raise ValueError(f"escalation {escalation_id} is already {esc['status']}")
    reviewer = ctx.actor
    customer_ctx = ctx.for_customer(esc["customer_id"])

    if not approve:
        with db.transaction():
            if not escalations.resolve(escalation_id, "denied", reviewer):
                raise ValueError(f"escalation {escalation_id} was resolved by someone else")
            if esc["offer_id"]:
                transactional.transition_offer(esc["offer_id"], "pending_review", "declined")
        audit.record(kind="human", caller=reviewer, tool="resolve_escalation",
                     detail={"escalation_id": escalation_id, "decision": "denied"})
        return {"escalation_id": escalation_id, "status": "denied", "order_id": None}

    prefix = "SUB" if esc["kind"] == "subscription" else "ORD"
    order_id = f"{prefix}-{uuid.uuid4().hex[:10].upper()}"
    with db.transaction():
        if not escalations.resolve(escalation_id, "approved", reviewer, order_id=order_id):
            raise ValueError(f"escalation {escalation_id} was resolved by someone else")
        if esc["kind"] == "order":
            inventory.decrement(esc["sku"], esc["size"] or "M", esc["quantity"])
        transactional.insert_order(order_id=order_id, customer_id=esc["customer_id"], kind=esc["kind"],
                                   amount=esc["amount"], sku=esc["sku"], size=esc["size"], quantity=esc["quantity"],
                                   plan=esc["plan"], idempotency_key=f"escalation:{escalation_id}")
        if esc["offer_id"]:
            transactional.transition_offer(esc["offer_id"], "pending_review", "accepted", order_id=order_id)
        points = runtime.invoke(customer_ctx, "earn_points", amount=esc["amount"], source=esc["kind"], ref_id=order_id)
    audit.record(kind="human", caller=reviewer, tool="resolve_escalation",
                 detail={"escalation_id": escalation_id, "decision": "approved", "order_id": order_id})
    return {"escalation_id": escalation_id, "status": "approved", "order_id": order_id,
            "points_earned": points["points_earned"], "points_balance": points["points_balance"]}


contracts.register(
    "compose_offer", owner=AGENT_NAME, allowed_callers=["lead_orchestrator"], handler=_compose_offer,
    input_model=ComposeIn, domains={"catalogue": "R", "transactional": "RW"},
    description="Compose a styling-subscription offer. Read-only with respect to money: nothing is charged.",
)
contracts.register(
    "accept_offer", owner=AGENT_NAME, allowed_callers=["storefront", "lead_orchestrator"], handler=_accept_offer,
    input_model=AcceptIn, domains={"loyalty": "R", "transactional": "RW"}, side_effect=True,
)
contracts.register(
    "commit_order", owner=AGENT_NAME, allowed_callers=["storefront", "lead_orchestrator"], handler=_commit_order,
    input_model=OrderIn, domains={"catalogue": "R", "inventory": "RW", "transactional": "RW", "loyalty": "R"},
    side_effect=True,
)
contracts.register(
    "resolve_escalation", owner=AGENT_NAME, allowed_callers=["human_reviewer"], handler=_resolve_escalation,
    input_model=ResolveIn, domains={"inventory": "RW", "transactional": "RW", "escalations": "RW"},
    side_effect=True, customer_scoped=False,
)
