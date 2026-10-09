"""TALLY — UC5 loyalty & gamification.

Owns the points ledger (append-only, idempotent per business event), tier

upgrades from year-to-date spend, redemption with bounds, and a small set of

challenges/badges computed from real behaviour.

"""

from pydantic import BaseModel, Field

from neutail.context import Context

from neutail.data import behavioural, loyalty, returns, transactional

from neutail.platform import contracts

AGENT_NAME = "tally_agent"

TIER_MULTIPLIER = {"Bronze": 1.0, "Silver": 1.25, "Gold": 1.5, "Platinum": 2.0}

TIER_ORDER = ["Bronze", "Silver", "Gold", "Platinum"]

TIER_SPEND = {"Bronze": 0, "Silver": 500, "Gold": 1500, "Platinum": 4000}  # £ year-to-date

POINTS_PER_POUND_VOUCHER = 100

MIN_REDEEM = 500

REWARDS = [

    {"id": "voucher5", "label": "£5 voucher", "points": 500},

    {"id": "voucher15", "label": "£15 voucher", "points": 1500},

    {"id": "express", "label": "Free express delivery for a month", "points": 800},

    {"id": "styling", "label": "One-off 30-min stylist session", "points": 2500},

]


class EarnIn(BaseModel):

    amount: float = Field(gt=0, le=10000)

    source: str = Field(pattern="^(order|subscription)$")

    ref_id: str = Field(min_length=3, max_length=64)


class RedeemIn(BaseModel):

    reward_id: str = Field(pattern="^(" + "|".join(r["id"] for r in REWARDS) + ")$")

    idempotency_key: str = Field(min_length=8, max_length=128)


def tier_for_spend(spend: float) -> str:

    tier = "Bronze"

    for name in TIER_ORDER:

        if spend >= TIER_SPEND[name]:

            tier = name

    return tier


def _status(customer_id: str) -> dict:

    row = loyalty.get(customer_id)

    if row is None:

        raise ValueError("no loyalty record for this customer")

    idx = TIER_ORDER.index(row["tier"])

    next_tier = TIER_ORDER[idx + 1] if idx + 1 < len(TIER_ORDER) else None

    to_next = max(0.0, TIER_SPEND[next_tier] - row["ytd_spend"]) if next_tier else 0.0

    progress = 1.0 if not next_tier else min(1.0, (row["ytd_spend"] - TIER_SPEND[row["tier"]]) /

                                             (TIER_SPEND[next_tier] - TIER_SPEND[row["tier"]]))

    return {

        "tier": row["tier"], "points_balance": row["points_balance"], "ytd_spend": round(row["ytd_spend"], 2),

        "multiplier": TIER_MULTIPLIER[row["tier"]], "next_tier": next_tier,

        "spend_to_next_tier": round(to_next, 2), "tier_progress": round(max(0.0, progress), 3),

        "voucher_value": round(row["points_balance"] / POINTS_PER_POUND_VOUCHER, 2),

        "rewards": [{**r, "affordable": row["points_balance"] >= r["points"]} for r in REWARDS],

    }


def _get_loyalty_status(ctx: Context) -> dict:

    return {**_status(ctx.customer_id), "recent_activity": loyalty.ledger(ctx.customer_id, limit=6)}


def _earn_points(ctx: Context, amount: float, source: str, ref_id: str) -> dict:

    row = loyalty.get(ctx.customer_id)

    if row is None:

        raise ValueError("no loyalty record for this customer")

    multiplier = TIER_MULTIPLIER[row["tier"]]

    points = round(amount * multiplier)

    applied = loyalty.apply_entry(ctx.customer_id, points, amount, source, ref_id, note=f"{multiplier}× on £{amount:.2f}")

    if not applied:  # replay of an event already credited

        points = (loyalty.ledger_entry(source, ref_id) or {}).get("delta", 0)

    upgraded_to = None

    current = loyalty.get(ctx.customer_id)

    earned_tier = tier_for_spend(current["ytd_spend"])

    if TIER_ORDER.index(earned_tier) > TIER_ORDER.index(current["tier"]):

        loyalty.set_tier(ctx.customer_id, earned_tier)  # upgrades only; no mid-year downgrades

        upgraded_to = earned_tier

    return {"points_earned": points, "points_balance": loyalty.get(ctx.customer_id)["points_balance"],

            "multiplier": multiplier, "tier_upgraded_to": upgraded_to, "replayed": not applied}


def _redeem_points(ctx: Context, reward_id: str, idempotency_key: str) -> dict:

    """Spend points on a reward from the catalogue."""

    reward = next(r for r in REWARDS if r["id"] == reward_id)

    row = loyalty.get(ctx.customer_id)

    if row is None:

        raise ValueError("no loyalty record for this customer")

    if reward["points"] < MIN_REDEEM:

        raise ValueError(f"minimum redemption is {MIN_REDEEM} points")

    applied = loyalty.apply_entry(ctx.customer_id, -reward["points"], 0.0, "redeem", idempotency_key,

                                  note=reward["label"])

    code = f"NT-{idempotency_key[-6:].upper()}"

    return {"redeemed": reward["label"], "points_spent": reward["points"], "voucher_code": code,

            "points_balance": loyalty.get(ctx.customer_id)["points_balance"], "replayed": not applied}


def _get_challenges(ctx: Context) -> dict:

    """Badges and streaks computed from real activity."""

    categories = transactional.category_counts(ctx.customer_id)

    fit_checks = behavioural.event_count(ctx.customer_id, "fit_check")

    weeks = behavioural.active_weeks(ctx.customer_id, weeks=8)

    n_returns = returns.count_for_customer(ctx.customer_id)

    n_orders = sum(categories.values())

    badges = [

        {"id": "explorer", "label": "Explorer", "desc": "Buy from 4 different categories",

         "progress": min(len(categories), 4), "goal": 4},

        {"id": "fit_pro", "label": "Fit Pro", "desc": "Check your fit 3 times before buying",

         "progress": min(fit_checks, 3), "goal": 3},

        {"id": "streak", "label": "On a roll", "desc": "Shop or browse in 4 of the last 8 weeks",

         "progress": min(weeks, 4), "goal": 4},

        {"id": "keeper", "label": "Keeper", "desc": "Keep 5 orders in a row with no returns",

         "progress": min(max(n_orders - n_returns, 0), 5), "goal": 5},

    ]

    for b in badges:

        b["earned"] = b["progress"] >= b["goal"]

    return {"badges": badges, "earned": sum(b["earned"] for b in badges)}


contracts.register(

    "get_loyalty_status", owner=AGENT_NAME,

    allowed_callers=["lead_orchestrator", "storefront", "concierge_agent", "admin", "external_mcp"],

    handler=_get_loyalty_status, domains={"loyalty": "R"},

)

contracts.register(

    "earn_points", owner=AGENT_NAME, allowed_callers=["concierge_agent"], handler=_earn_points,

    input_model=EarnIn, domains={"loyalty": "RW"}, side_effect=True,

)

contracts.register(

    "redeem_points", owner=AGENT_NAME, allowed_callers=["storefront", "lead_orchestrator"], handler=_redeem_points,

    input_model=RedeemIn, domains={"loyalty": "RW"}, side_effect=True,

)

contracts.register(

    "get_challenges", owner=AGENT_NAME, allowed_callers=["lead_orchestrator", "storefront", "admin"],

    handler=_get_challenges, domains={"transactional": "R", "behavioural": "R", "returns": "R"},

)