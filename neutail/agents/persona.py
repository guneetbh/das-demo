"""PERSONA — UC1 segmentation (§04: provides get_customer_segment).

Resolves affluent/value live from CRM + loyalty + behavioural signals.
Segment is never stored — it's derived on every call, per the demo
script's "PERSONA resolves ... live" (§06 step 2).
"""

from neutail import contracts, mcp_client
from neutail.db import get_connection

AGENT_NAME = "persona_agent"

AFFLUENT_TIERS = {"Gold", "Platinum"}


def _get_customer_segment(customer_id: str) -> dict:
    conn = get_connection()
    customer = conn.execute(
        "SELECT tenure_months FROM customers WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    if customer is None:
        conn.close()
        raise ValueError(f"unknown customer_id: {customer_id}")

    loyalty = conn.execute(
        "SELECT tier FROM loyalty WHERE customer_id = ?", (customer_id,)
    ).fetchone()
    tier = loyalty["tier"] if loyalty else "Bronze"

    tags = conn.execute(
        """SELECT DISTINCT c.category
           FROM behavioural b JOIN catalogue c ON c.sku = b.sku
           WHERE b.customer_id = ?""",
        (customer_id,),
    ).fetchall()
    conn.close()

    segment = "affluent" if tier in AFFLUENT_TIERS else "value"
    return {
        "segment": segment,
        "tenure_months": customer["tenure_months"],
        "preference_tags": sorted(row["category"] for row in tags),
    }


contracts.register(
    "get_customer_segment",
    allowed_callers=["persona_agent", "lead_orchestrator", "mcp_client"],
    handler=_get_customer_segment,
)


async def run(customer_id: str) -> dict:
    """PERSONA's entry point — the Orchestrator calls this (Fig. 02, step 2).
    Dispatches via mcp_client (real MCP, in-process) rather than calling
    runtime.invoke_tool() directly — see neutail/mcp_client.py."""
    return await mcp_client.call_tool(AGENT_NAME, "get_customer_segment", customer_id=customer_id)
