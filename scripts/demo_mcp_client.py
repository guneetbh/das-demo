"""Connects to the external MCP adapter (:8765) the way Claude Desktop or any
MCP client would, and shows: the tool catalogue, an allowed read, a write
the policy denies, and that there is no longer a way to self-declare a
privileged caller.

    python -m neutail.transports.mcp_server     # in another terminal
    python scripts/demo_mcp_client.py
"""

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.sse import sse_client

MCP_URL = "http://127.0.0.1:8765/sse"
MCP_KEY = os.environ.get("NEUTAIL_MCP_KEY", "dev-mcp-key")


async def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles can't print arrows in cp1252
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    async with sse_client(MCP_URL, headers={"Authorization": f"Bearer {MCP_KEY}"}) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print(f"Connected as external_mcp — {len(tools.tools)} tools: {', '.join(sorted(t.name for t in tools.tools))}\n")

            r = await session.call_tool("get_loyalty_status", {"customer_id": "CUST-PRIYA"})
            print("1. read  get_loyalty_status →", r.content[0].text[:160], "\n")

            r = await session.call_tool("commit_order", {"customer_id": "CUST-PRIYA", "sku": "P-DRS-001", "size": "M"})
            print("2. write commit_order → isError:", r.isError, "·", r.content[0].text, "\n")

            r = await session.call_tool("rank_products", {"customer_id": "CUST-PRIYA", "query": "jeans",
                                                          "caller": "lead_orchestrator"})
            print("3. self-declared caller='lead_orchestrator' → isError:", r.isError, "·", r.content[0].text[:160])


if __name__ == "__main__":
    asyncio.run(main())
