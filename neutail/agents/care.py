"""CARE — UC4 autonomous service; hosts CONCIERGE.

Resolves the contact context with PII masked, decides whether CONCIERGE may
pitch at all (tier + marketing consent — the PII & consent policy), and
serves order history for self-service (including returns via GRADE).
"""

from neutail.context import Context
from neutail.data import crm, loyalty, transactional
from neutail.platform import contracts

AGENT_NAME = "care_agent"
UPSELL_ELIGIBLE_TIERS = {"Gold", "Platinum"}


def mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    masked = name[0] + "*" if len(name) <= 2 else name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


def _resolve_contact(ctx: Context) -> dict:
    customer = crm.require_customer(ctx.customer_id)
    tier = (loyalty.get(ctx.customer_id) or {}).get("tier", "Bronze")
    consent = bool(customer["marketing_consent"])
    eligible_tier = tier in UPSELL_ELIGIBLE_TIERS
    if not consent:
        reason = "no marketing consent on file"
    elif not eligible_tier:
        reason = f"{tier} tier — styling subscriptions open from Gold"
    else:
        reason = None
    return {
        "contact_channel": "email",
        "masked_contact": mask_email(customer["email"]),
        "tier": tier,
        "upsell_flag": consent and eligible_tier,
        "upsell_blocked_reason": reason,
    }


def _order_history(ctx: Context) -> dict:
    """Orders and subscriptions, newest first, with return status."""
    return {"orders": transactional.history(ctx.customer_id)}


contracts.register(
    "resolve_contact", owner=AGENT_NAME, allowed_callers=["lead_orchestrator", "concierge_agent", "admin"],
    handler=_resolve_contact, domains={"crm": "R", "loyalty": "R"},
)
contracts.register(
    "order_history", owner=AGENT_NAME, allowed_callers=["storefront", "lead_orchestrator", "admin"],
    handler=_order_history, domains={"transactional": "R", "catalogue": "R", "returns": "R"},
)
