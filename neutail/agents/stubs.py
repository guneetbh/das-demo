"""Supporting agents from the deck that the Personalisation Assistant draws
on: ONEBASKET (identity), SCOUT (trend), PACE (price), GRADE (returns).

Deliberately thin — but each has a real tool contract, owner, data grant and
audit trail, so the arrows on slides 4-5 exist in code and can be deepened
without touching their callers.
"""

import uuid

from pydantic import BaseModel, Field

from neutail.context import Context
from neutail.data import behavioural, catalogue, crm, inventory, returns, transactional
from neutail.platform import contracts

# --------------------------------------------------------------------------- ONEBASKET


class ResolveIdentityIn(BaseModel):
    handle: str = Field(min_length=3, max_length=120)


def _resolve_identity(ctx: Context, handle: str) -> dict:
    """Map a login handle (customer ID or email) to one customer identity."""
    customer = crm.get_customer(handle) or crm.find_by_email(handle)
    if customer is None:
        raise ValueError("no customer matches that handle")
    return {"customer_id": customer["customer_id"], "name": customer["name"], "channel": "web"}


contracts.register(
    "resolve_identity", owner="onebasket_agent", allowed_callers=["storefront_auth", "admin"],
    handler=_resolve_identity, input_model=ResolveIdentityIn, domains={"crm": "R"}, customer_scoped=False,
)

# --------------------------------------------------------------------------- SCOUT


class TrendIn(BaseModel):
    skus: list[str] = Field(default_factory=list, max_length=400)


def _get_trend_signal(ctx: Context, skus: list[str]) -> dict:
    """Trend flags per SKU plus estate-wide 7-day category momentum."""
    items = catalogue.many(skus)
    momentum = behavioural.category_momentum(days=7)
    top = max(momentum.values()) if momentum else 1
    return {
        "trending_skus": sorted(s for s, row in items.items() if row["trending"]),
        "category_momentum": {c: round(n / top, 3) for c, n in momentum.items()},
    }


contracts.register(
    "get_trend_signal", owner="scout_agent", allowed_callers=["muse_agent", "lead_orchestrator", "admin"],
    handler=_get_trend_signal, input_model=TrendIn, domains={"catalogue": "R", "behavioural": "R"},
    customer_scoped=False,
)

# --------------------------------------------------------------------------- PACE

PRICE_BAND = 0.05  # the ±5% daily guardrail from the deck


class PriceIn(BaseModel):
    skus: list[str] = Field(min_length=1, max_length=400)


def _get_price_context(ctx: Context, skus: list[str]) -> dict:
    """Current price, markdown and the ±5% guardrail band per SKU."""
    items = catalogue.many(skus)
    stock = inventory.stock_level(list(items))
    out = {}
    for sku, row in items.items():
        price = row["price"]
        was = row["was_price"]
        out[sku] = {
            "price": price,
            "was_price": was,
            "on_sale": bool(was and was > price),
            "discount_pct": round((1 - price / was) * 100) if was and was > price else 0,
            "band": [round(price * (1 - PRICE_BAND), 2), round(price * (1 + PRICE_BAND), 2)],
            "low_stock": stock.get(sku, 0) <= 5,
        }
    return {"prices": out}


contracts.register(
    "get_price_context", owner="pace_agent",
    allowed_callers=["muse_agent", "concierge_agent", "lead_orchestrator", "storefront", "admin"],
    handler=_get_price_context, input_model=PriceIn, domains={"catalogue": "R", "inventory": "R"},
    customer_scoped=False,
)

# --------------------------------------------------------------------------- GRADE

REASON_CODES = ("too_small", "too_large", "not_as_described", "changed_mind", "defective")


class RecordReturnIn(BaseModel):
    order_id: str = Field(min_length=3, max_length=64)
    reason_code: str = Field(pattern="^(" + "|".join(REASON_CODES) + ")$")
    condition_grade: str = Field(default="A", pattern="^[ABC]$")


def _record_return(ctx: Context, order_id: str, reason_code: str, condition_grade: str) -> dict:
    """Grade a returned item and write its reason — the signal TAILOR learns from."""
    order = transactional.get_order(order_id)
    if order is None or order["customer_id"] != ctx.customer_id:
        raise ValueError("no such order for this customer")
    if order["kind"] != "order" or not order["sku"]:
        raise ValueError("only product orders can be returned")
    if returns.for_order(order_id):
        raise ValueError("this order has already been returned")
    return_id = f"RET-{uuid.uuid4().hex[:10].upper()}"
    returns.insert(return_id=return_id, order_id=order_id, customer_id=ctx.customer_id, sku=order["sku"],
                   size_returned=order["size"] or "M", reason_code=reason_code, condition_grade=condition_grade)
    item = catalogue.get(order["sku"]) or {}
    return {"return_id": return_id, "order_id": order_id, "sku": order["sku"], "category": item.get("category"),
            "reason_code": reason_code, "condition_grade": condition_grade,
            "feeds": "TAILOR fit profile" if reason_code in ("too_small", "too_large") else "RECLAIM recovery"}


contracts.register(
    "record_return", owner="grade_agent", allowed_callers=["storefront", "care_agent", "lead_orchestrator"],
    handler=_record_return, input_model=RecordReturnIn,
    domains={"catalogue": "R", "transactional": "R", "returns": "RW"}, side_effect=True,
)
