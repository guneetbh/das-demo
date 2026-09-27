"""Standalone MCP demo script — connects to the external server (:8765)
exactly the way Claude Desktop or any other MCP client would, and proves
three things live: the tool catalog is real (13 tools), a read succeeds,
and a write attempt gets denied by the same policy engine everything
else in this system uses. No API key, no live model — this is pure
protocol + policy, works identically on fallback or live.

Run: uvicorn/streamlit don't need to be up for this one — only
`python -m neutail.mcp_server` (the external :8765 server) does.

    python3 demo_mcp_client.py
"""

import asyncio

from mcp import ClientSession
from mcp.client.sse import sse_client

MCP_URL = "http://127.0.0.1:8765/sse"


async def main():
    async with sse_client(MCP_URL) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"Connected to {MCP_URL} — {len(tools.tools)} tools available:")
            print("  " + ", ".join(sorted(t.name for t in tools.tools)))
            print()

            print("1. A read any external caller can make (default caller: mcp_client):")
            r = await session.call_tool("get_loyalty_status", {"customer_id": "CUST-PRIYA"})
            print("   get_loyalty_status(CUST-PRIYA) ->", r.content[0].text)
            print()

            print("2. A write path the SAME external caller is denied — no override, no bypass:")
            r2 = await session.call_tool(
                "rank_products",
                {"customer_id": "CUST-PRIYA", "segment": "affluent", "query": "jeans"},
            )
            print("   rank_products(...) -> isError:", r2.isError)
            print("  ", r2.content[0].text)
            print()

            print("3. Same tool, self-declared as an internal caller that IS allow-listed —")
            print("   proves the policy is checked by identity, not by transport:")
            r3 = await session.call_tool(
                "rank_products",
                {"customer_id": "CUST-PRIYA", "segment": "affluent", "query": "jeans", "caller": "lead_orchestrator"},
            )
            print("   rank_products(..., caller='lead_orchestrator') -> isError:", r3.isError)


if __name__ == "__main__":
    asyncio.run(main())
