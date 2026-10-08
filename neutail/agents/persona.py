"""PERSONA — UC1 profiling & segmentation. Publishes the segment downstream
(the orchestrator puts it on the context object; nobody else re-derives it).

Score-based rather than a single tier rule: tier, tenure, 90-day spend and
engagement recency each contribute, and the result carries its confidence
and the signals behind it so downstream agents (and the UI) can explain it.
"""

from datetime import datetime, timezone

from neutail.context import Context
from neutail.data import behavioural, crm, loyalty, transactional
from neutail.platform import contracts

AGENT_NAME = "persona_agent"
TIER_POINTS = {"Bronze": 0.0, "Silver": 1.0, "Gold": 2.0, "Platinum": 3.0}
AFFLUENT_THRESHOLD = 3.0


def _days_since(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    return (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)).days


def _get_customer_segment(ctx: Context) -> dict:
    customer = crm.require_customer(ctx.customer_id)
    tier = (loyalty.get(ctx.customer_id) or {}).get("tier", "Bronze")
    stats = transactional.stats(ctx.customer_id)
    affinity = behavioural.category_affinity(ctx.customer_id)
    for category, n in transactional.category_counts(ctx.customer_id).items():
        affinity[category] = affinity.get(category, 0) + 2 * n
    days_since_active = _days_since(behavioural.last_activity(ctx.customer_id) or stats["last_order_at"])

    signals = {
        "tier": tier,
        "tenure_months": customer["tenure_months"],
        "spend_90d": round(stats["spend_90d"], 2),
        "orders": stats["n_orders"],
        "days_since_active": days_since_active,
    }
    score = TIER_POINTS.get(tier, 0.0)
    score += 1.0 if customer["tenure_months"] >= 12 else 0.0
    score += 2.0 if stats["spend_90d"] >= 1000 else 1.0 if stats["spend_90d"] >= 400 else 0.0
    score += 0.5 if days_since_active is not None and days_since_active <= 14 else 0.0

    segment = "affluent" if score >= AFFLUENT_THRESHOLD else "value"
    if customer["tenure_months"] < 3:
        micro = "new_customer"
    elif days_since_active is not None and days_since_active > 60:
        micro = "lapsing"
    elif segment == "affluent" and tier in ("Gold", "Platinum"):
        micro = "loyal_premium"
    elif segment == "affluent":
        micro = "rising_premium"
    elif stats["n_orders"] >= 6:
        micro = "frequent_value"
    else:
        micro = "value_explorer"

    # Distance from the decision boundary, scaled into 0.55-0.99.
    confidence = round(min(0.99, 0.55 + abs(score - AFFLUENT_THRESHOLD) / 6), 2)
    return {
        "segment": segment,
        "micro_segment": micro,
        "score": score,
        "confidence": confidence,
        "signals": signals,
        "preference_tags": [c for c, _ in sorted(affinity.items(), key=lambda kv: -kv[1])][:4],
        "marketing_consent": bool(customer["marketing_consent"]),
        "first_name": customer["name"].split()[0],
    }


contracts.register(
    "get_customer_segment",
    owner=AGENT_NAME,
    allowed_callers=["lead_orchestrator", "muse_agent", "concierge_agent", "care_agent", "tally_agent", "storefront",
                     "admin", "external_mcp"],
    handler=_get_customer_segment,
    domains={"crm": "R", "loyalty": "R", "behavioural": "R", "transactional": "R"},
    description="Resolve a customer's segment, micro-segment and preference tags live from CRM, loyalty, "
                "behavioural and transactional data.",
)
