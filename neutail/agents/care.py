"""CARE — UC4 contact resolution (§04: provides resolve_contact).

Masks raw contact fields per the PII & consent policy (§05) and flags
whether this customer is worth CONCIERGE composing an upsell offer for.
"""

from neutail import contracts, runtime
from neutail.db import get_connection

AGENT_NAME = "care_agent"

UPSELL_ELIGIBLE_TIERS = {"Gold", "Platinum"}


def _mask_email(email: str) -> str:
    name, _, domain = email.partition("@")
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[0] + "*" * (len(name) - 2) + name[-1]
    return f"{masked}@{domain}"


def _resolve_contact(customer_id: str) -> dict:
    conn = get_connection()
    customer = conn.execute(
        "SELECT email FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if customer is None:
        conn.close()
        raise ValueError(f"unknown customer_id: {customer_id}")

    loyalty = conn.execute(
        "SELECT tier FROM loyalty WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    conn.close()

    tier = loyalty["tier"] if loyalty else "Bronze"
    return {
        "customer_id": customer_id,
        "contact_channel": "email",
        "masked_contact": _mask_email(customer["email"]),
        "upsell_flag": tier in UPSELL_ELIGIBLE_TIERS,
    }


contracts.register(
    "resolve_contact",
    allowed_callers=["care_agent", "lead_orchestrator", "concierge_agent"],
    handler=_resolve_contact,
)


def run(customer_id: str) -> dict:
    """CARE's entry point — the Orchestrator calls this (Fig. 02, step 6)."""
    return runtime.invoke_tool(AGENT_NAME, "resolve_contact", customer_id=customer_id)
