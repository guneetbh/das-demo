"""MCP layer — the canonical dispatch surface for every tool call in
Neu.Tail, internal and external alike.

This used to be (and still can be run as) a database-interaction surface
for external clients only. It's now more than that: every one of the 9
policy-checked tool contracts (not just 3) is registered here, and
`neutail/mcp_client.py` uses this exact module — imported in-process, no
network — as the internal dispatch path every agent goes through instead
of calling `runtime.invoke_tool()` directly. One registry, two transports:

- **External**: `python -m neutail.mcp_server` runs it standalone over
  HTTP/SSE on `:8765`, for Claude Desktop / Claude Code / any external MCP
  client — unchanged command from before.
- **Internal**: any process that needs to dispatch a tool call imports
  this module (side-effect-free — building the `FastMCP` object and
  registering tools doesn't start a server) and opens its own in-process
  session against it (`mcp_client.py`). Same source, same tool
  implementations, same policy rules; two separate live instances (one
  per OS process) by necessity of process isolation, not one shared
  object across processes.

Every policy-checked tool takes `caller: str = "mcp_client"` as an
explicit parameter. Internal callers always pass their real identity
(`"persona_agent"`, `"muse_agent"`, ...) — never rely on the default.
External callers who don't specify one still default to `"mcp_client"`,
which stays allow-listed on exactly the same 3 tools as before this
change (`get_customer_segment`, `get_fit_profile`, `get_loyalty_status`);
the 6 newly-exposed tools are unreachable to an external caller unless
they explicitly self-declare a privileged caller name. That's not a new
gap: `POST /tools/{name}/invoke` already accepts a fully self-declared
`caller` today with zero auth, and `"lead_orchestrator"` is on nearly
every tool's allow-list — the write-capable "exploit" already exists via
`:8000` today. Unification adds the same one reachable via `:8765` too,
not a new one. The policy engine doesn't know or care which transport a
caller arrived over — default-deny, allow-listed per tool, same as ever.

Exception remapping: FastMCP's default behavior collapses any exception a
tool handler raises into an untyped `isError=True` text string — losing
whether it was a `PolicyDenied`, a `LookupError`, or a `ValueError`, which
matters because `api.py` maps those to HTTP 403/404/400 respectively.
Each policy-checked tool below catches those 3 specifically and returns
(not raises) an explicit `CallToolResult` carrying `structuredContent =
{"error_type": ..., "reason": ...}` — `mcp_client.call_tool()` reads that
back and reconstructs the real exception. Anything unmapped (a genuine
bug) still surfaces as `isError=True` with no recognized `error_type`,
which `mcp_client.py` turns into a plain `RuntimeError` rather than
failing to parse it.

Sync handlers, offloaded to a thread — deliberately, not an oversight.
`runtime.invoke_tool()`, every registered tool handler (`_rank_products`,
`_commit_subscription`, ...), `policy.py`, and any cross-agent call one
handler makes to another tool (MUSE calling TAILOR's `get_fit_profile`
inside its evaluator loop, CONCIERGE calling CARE/SENTRY/TALLY inside a
commit) all stay exactly as synchronous as they were before this file
existed — none of that logic needed to change, and re-routing an
already-server-side call back out through another MCP hop would be
circular for no benefit. What *does* need care: this server's own event
loop is shared with the FastAPI process when used internally (the
in-memory session's background task runs on the caller's loop), so a
tool wrapper that called a slow synchronous handler (a live reasoning-
tier call can take 15-20s) directly would freeze that entire shared
loop — every other concurrent request, not just this one. Each of the 9
policy-checked tool wrappers below is `async def` and runs `_dispatch`
via `asyncio.to_thread(...)` for exactly this reason: the synchronous
chain gets a worker thread, the event loop stays free.

Run: python -m neutail.mcp_server        # serves SSE on :8765
"""

import asyncio
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from neutail import admin, policy, runtime, vector_store
from neutail.agents import care, concierge, muse, persona, sentry, tailor, tally  # noqa: F401 — imports register all 9 tool contracts
from neutail.db import get_connection

MCP_CALLER = "mcp_client"

mcp = FastMCP(
    "neutail-db",
    instructions=(
        "Full access to Neu.Tail's agent mesh, database (SQLite), and vector store (Chroma), "
        "gated by the same default-deny policy every internal caller is subject to. Most callers "
        "should only expect success from get_customer_segment/get_fit_profile/get_loyalty_status/ "
        "search_products/get_catalogue_item/get_audit_log/get_business_outcomes — the rest "
        "(rank_products, commit_subscription, commit_order, resolve_contact, check_payment_policy, "
        "earn_points) require a caller identity that's actually allow-listed for them."
    ),
    host="127.0.0.1",
    port=8765,
)


def _ok(payload: dict) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, default=str))],
        structuredContent=payload,
        isError=False,
    )


def _err(error_type: str, reason: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=f"{error_type}: {reason}")],
        structuredContent={"error_type": error_type, "reason": reason},
        isError=True,
    )


def _dispatch(caller: str, tool: str, **kwargs) -> CallToolResult:
    """Shared by every policy-checked tool below — calls the same
    runtime.invoke_tool() chokepoint every internal agent-to-agent call
    uses, and maps its 3 possible exception types to a structured error
    result instead of letting FastMCP's default handling erase which one
    it was."""
    try:
        return _ok(runtime.invoke_tool(caller, tool, **kwargs))
    except policy.PolicyDenied as exc:
        return _err("PolicyDenied", exc.reason)
    except LookupError as exc:
        return _err("LookupError", str(exc))
    except ValueError as exc:
        return _err("ValueError", str(exc))


# --------------------------------------------------------------------------- policy-checked (9)
@mcp.tool()
async def get_customer_segment(customer_id: str, caller: str = MCP_CALLER) -> CallToolResult:
    """Resolve a customer's segment (affluent/value) live from CRM + loyalty + behavioural data."""
    return await asyncio.to_thread(_dispatch, caller, "get_customer_segment", customer_id=customer_id)


@mcp.tool()
async def get_fit_profile(customer_id: str, category: str, caller: str = MCP_CALLER) -> CallToolResult:
    """Get a customer's fit/return-risk guidance for a product category (e.g. 'jeans', 'dress')."""
    return await asyncio.to_thread(_dispatch, caller, "get_fit_profile", customer_id=customer_id, category=category)


@mcp.tool()
async def get_loyalty_status(customer_id: str, caller: str = MCP_CALLER) -> CallToolResult:
    """Get a customer's loyalty tier, points balance, and YTD spend."""
    return await asyncio.to_thread(_dispatch, caller, "get_loyalty_status", customer_id=customer_id)


@mcp.tool()
async def earn_points(customer_id: str, amount: float, source: str = "purchase", caller: str = MCP_CALLER) -> CallToolResult:
    """Award loyalty points for a completed purchase — amount x the customer's tier multiplier."""
    return await asyncio.to_thread(_dispatch, caller, "earn_points", customer_id=customer_id, amount=amount, source=source)


@mcp.tool()
async def resolve_contact(customer_id: str, caller: str = MCP_CALLER) -> CallToolResult:
    """Resolve a customer's contact/service context — masks PII, flags upsell eligibility by tier."""
    return await asyncio.to_thread(_dispatch, caller, "resolve_contact", customer_id=customer_id)


@mcp.tool()
async def check_payment_policy(
    customer_id: str, amount: float, kind: str = "subscription", sku: str | None = None, caller: str = MCP_CALLER
) -> CallToolResult:
    """Check a charge against policy bounds — approves inside them, else escalates for human review."""
    return await asyncio.to_thread(
        _dispatch, caller, "check_payment_policy", customer_id=customer_id, amount=amount, kind=kind, sku=sku
    )


@mcp.tool()
async def rank_products(
    customer_id: str, segment: str, query: str, top_n: int = 6, caller: str = MCP_CALLER
) -> CallToolResult:
    """Rank product recommendations for a customer — the full MUSE discovery pipeline (vector search,
    evaluator loop, diversity cap, live-model or fallback ranking). Can take 15-20s on a live model —
    this is exactly why this wrapper is async + threaded rather than a plain sync call."""
    return await asyncio.to_thread(
        _dispatch, caller, "rank_products", customer_id=customer_id, segment=segment, query=query, top_n=top_n
    )


@mcp.tool()
async def commit_subscription(
    customer_id: str, amount: float, plan: str = "standard", caller: str = MCP_CALLER
) -> CallToolResult:
    """Commit a styling subscription — requires CARE + SENTRY + TALLY; escalates instead of denying
    if outside policy bounds."""
    return await asyncio.to_thread(
        _dispatch, caller, "commit_subscription", customer_id=customer_id, amount=amount, plan=plan
    )


@mcp.tool()
async def commit_order(customer_id: str, sku: str, quantity: int = 1, caller: str = MCP_CALLER) -> CallToolResult:
    """Commit a product purchase — same policy/points chokepoints as a subscription, its own SENTRY threshold."""
    return await asyncio.to_thread(_dispatch, caller, "commit_order", customer_id=customer_id, sku=sku, quantity=quantity)


# --------------------------------------------------------------------------- read-only reporting (4, unchanged)
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
