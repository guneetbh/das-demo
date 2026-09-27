"""MCP layer — exposes Neu.Tail's data (the SQLite store + the Chroma
vector store) to any MCP client (Claude Desktop, Claude Code, a custom
agent) over HTTP/SSE, the same way neutail/api.py exposes it over REST.

This is deliberately a *database-interaction* surface, not a copy of the
whole agent mesh: read-only lookups and search, nothing that commits an
order or a subscription. Three of the seven tools below route through
the same `runtime.invoke_tool()` chokepoint every agent uses — policy-
checked and audit-logged exactly like an internal agent-to-agent call —
under the caller identity "mcp_client", explicitly added to those three
tools' allow-lists (see persona.py/tailor.py/tally.py). Calling anything
NOT on an allow-list as "mcp_client" (e.g. rank_products, commit_order)
raises the same PolicyDenied a rogue internal caller would get — the
policy engine doesn't know or care that the caller arrived over MCP
instead of a Python import.

The other four (search_products, get_catalogue_item, get_audit_log,
get_business_outcomes) are read-only reporting queries with no tool
contract, same precedent as api.py's /catalogue/{sku}, /audit,
/admin/outcomes, /admin/vector_search.

Run: python -m neutail.mcp_server        # serves SSE on :8765
"""

from mcp.server.fastmcp import FastMCP

from neutail import admin, runtime, vector_store
from neutail.agents import persona, tailor, tally  # noqa: F401 — imports register their tool contracts
from neutail.db import get_connection

MCP_CALLER = "mcp_client"

mcp = FastMCP(
    "neutail-db",
    instructions=(
        "Read-only access to Neu.Tail's customer/product database (SQLite) and its product "
        "vector store (Chroma). Use search_products for free-text product search, "
        "get_catalogue_item for a specific SKU, get_customer_segment/get_fit_profile/"
        "get_loyalty_status for a specific customer, and get_audit_log/get_business_outcomes "
        "for what the system has done so far. Nothing here places an order or a subscription."
    ),
    host="127.0.0.1",
    port=8765,
)


@mcp.tool()
def get_customer_segment(customer_id: str) -> dict:
    """Resolve a customer's segment (affluent/value) live from CRM + loyalty + behavioural data."""
    return runtime.invoke_tool(MCP_CALLER, "get_customer_segment", customer_id=customer_id)


@mcp.tool()
def get_fit_profile(customer_id: str, category: str) -> dict:
    """Get a customer's fit/return-risk guidance for a product category (e.g. 'jeans', 'dress')."""
    return runtime.invoke_tool(MCP_CALLER, "get_fit_profile", customer_id=customer_id, category=category)


@mcp.tool()
def get_loyalty_status(customer_id: str) -> dict:
    """Get a customer's loyalty tier, points balance, and YTD spend."""
    return runtime.invoke_tool(MCP_CALLER, "get_loyalty_status", customer_id=customer_id)


@mcp.tool()
def search_products(query: str, top_k: int = 10) -> dict:
    """Semantic product search against the vector database — free text in, ranked SKUs with
    similarity scores out. Reports which backend served it (chroma vs. token-overlap fallback)."""
    hits = vector_store.semantic_search(query, top_k=top_k)
    results = []
    if hits:
        conn = get_connection()
        placeholders = ",".join("?" * len(hits))
        rows = {
            row["sku"]: dict(row)
            for row in conn.execute(
                f"SELECT * FROM catalogue WHERE sku IN ({placeholders})",
                tuple(h["sku"] for h in hits),
            ).fetchall()
        }
        conn.close()
        results = [{**rows[h["sku"]], "score": h["score"]} for h in hits if h["sku"] in rows]
    return {"query": query, "backend": vector_store.backend(), "results": results}


@mcp.tool()
def get_catalogue_item(sku: str) -> dict:
    """Look up a single product by SKU."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM catalogue WHERE sku = ?", (sku,)).fetchone()
    conn.close()
    if row is None:
        raise ValueError(f"no such SKU: {sku}")
    return dict(row)


@mcp.tool()
def get_audit_log(limit: int = 20) -> dict:
    """Tail of the audit trail — every tool call and model call, policy-checked, most recent first."""
    return {"entries": runtime.recent_audit_log(limit)}


@mcp.tool()
def get_business_outcomes() -> dict:
    """Business outcomes computed from the seeded population — return-rate guided-vs-baseline,
    search-to-purchase proxy, upsell counts. See neutail/admin.py for what each number does and
    doesn't claim."""
    return admin.business_outcomes()


if __name__ == "__main__":
    mcp.run(transport="sse")
