"""External MCP adapter — the agent mesh's tool contracts over MCP (HTTP/SSE,
:8765) for Claude Desktop, Claude Code or any MCP client.

Authenticated: every request must carry `Authorization: Bearer <NEUTAIL_MCP_KEY>`.
The connection acts as the `external_mcp` principal, so it gets exactly what
the contracts allow that principal (read-only tools) — there is no `caller`
parameter to self-declare a more privileged identity. Inside the process,
agents call each other through runtime.invoke() directly; MCP is the
standard *external* surface, not an internal hop.

Run: python -m neutail.transports.mcp_server
"""

import asyncio
import json

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, TextContent

from neutail import agents, config, data  # noqa: F401 — registers contracts
from neutail.context import Context
from neutail.data import catalogue
from neutail.platform import audit, policy, runtime, vector_store

PRINCIPAL = "external_mcp"
mcp = FastMCP(
    "neutail",
    instructions="Neu.Tail agent mesh, read-only for external clients: customer segment, fit guidance, loyalty "
                 "status, catalogue search. Write tools exist but are denied to this principal by policy.",
    host="127.0.0.1",
    port=8765,
)


def _result(payload: dict, error: str | None = None) -> CallToolResult:
    if error:
        return CallToolResult(content=[TextContent(type="text", text=error)],
                              structuredContent={"error": error}, isError=True)
    return CallToolResult(content=[TextContent(type="text", text=json.dumps(payload, default=str))],
                          structuredContent=payload, isError=False)


def _invoke(tool: str, **args) -> CallToolResult:
    ctx = Context(principal=PRINCIPAL, role="external", actor=PRINCIPAL)
    audit.current_trace.set(ctx.trace_id)
    audit.current_actor.set(PRINCIPAL)
    try:
        return _result(runtime.invoke(ctx, tool, **args))
    except policy.PolicyDenied as exc:
        return _result({}, f"PolicyDenied: {exc.reason}")
    except (LookupError, ValueError) as exc:
        return _result({}, f"{type(exc).__name__}: {exc}")


@mcp.tool()
async def get_customer_segment(customer_id: str) -> CallToolResult:
    """Segment, micro-segment and preference tags for a customer."""
    return await asyncio.to_thread(_invoke, "get_customer_segment", customer_id=customer_id)


@mcp.tool()
async def get_fit_profile(customer_id: str, category: str) -> CallToolResult:
    """Fit guidance and personal return risk for a category (dress, top, jeans, shoes, accessories, jacket, athleisure)."""
    return await asyncio.to_thread(_invoke, "get_fit_profile", customer_id=customer_id, category=category)


@mcp.tool()
async def get_loyalty_status(customer_id: str) -> CallToolResult:
    """Loyalty tier, points balance, progress to next tier."""
    return await asyncio.to_thread(_invoke, "get_loyalty_status", customer_id=customer_id)


@mcp.tool()
async def rank_products(customer_id: str, query: str) -> CallToolResult:
    """Full MUSE ranking — denied to external clients by policy; kept to demonstrate the gate."""
    return await asyncio.to_thread(_invoke, "rank_products", customer_id=customer_id, query=query)


@mcp.tool()
async def commit_order(customer_id: str, sku: str, size: str) -> CallToolResult:
    """Place an order — denied to external clients by policy; kept to demonstrate the gate."""
    return await asyncio.to_thread(_invoke, "commit_order", customer_id=customer_id, sku=sku, size=size,
                                   idempotency_key="external-attempt")


def _search(query: str, top_k: int) -> dict:
    hits = vector_store.semantic_search(query, top_k=min(top_k, 25), caller=PRINCIPAL)
    with data.system_scope():
        rows = catalogue.many([h["sku"] for h in hits])
    keep = ("sku", "name", "category", "price", "tier", "colour")
    return {"query": query, "backend": vector_store.backend(),
            "results": [{**{k: rows[h["sku"]][k] for k in keep}, "score": h["score"]} for h in hits if h["sku"] in rows]}


@mcp.tool()
async def search_catalogue(query: str, top_k: int = 10) -> dict:
    """Semantic product search — public catalogue data only."""
    return await asyncio.to_thread(_search, query, top_k)


class _BearerAuth:
    """ASGI middleware: reject any request without the configured MCP key."""

    def __init__(self, app, key: str):
        self.app, self.key = app, key

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            if headers.get(b"authorization", b"").decode() != f"Bearer {self.key}":
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain")]})
                await send({"type": "http.response.body", "body": b"missing or invalid MCP key"})
                return
        await self.app(scope, receive, send)


def main() -> None:
    import uvicorn

    key = next(k for k, (name, _) in config.STAFF_KEYS.items() if name == PRINCIPAL)
    uvicorn.run(_BearerAuth(mcp.sse_app(), key), host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
