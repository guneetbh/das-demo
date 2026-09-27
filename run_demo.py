"""Walks the eight demo-script steps from §06 of the architecture doc, live,
against the seeded SQLite store. Run `python -m neutail.seed` first.

Async — every agent call now goes through mcp_client (real MCP, in-process,
see neutail/mcp_client.py) rather than calling runtime.invoke_tool()
directly, so this script opens that session once at startup and closes it
at the end, same as the FastAPI app's lifespan hook does.
"""

import asyncio

from neutail import mcp_client
from neutail import orchestrator as orch
from neutail import runtime
from neutail.agents import concierge


def rule():
    print("-" * 78)


def show_results(results):
    for r in results:
        print(f"    {r['sku']:<12} {r['name']:<24} {r['tier']:<14} ${r['price']:<7.2f} "
              f"risk={r['return_risk']:.0%}  {r['reason']}")


async def main():
    await mcp_client.init()
    try:
        print("NEU.TAIL — DEMO SCRIPT WALKTHROUGH (§06)")
        rule()

        print("\n① / ② / ③ — orchestration, profiling, discovery (side by side)")
        for customer_id, session_id in [("CUST-PRIYA", "sess-priya-day1"), ("CUST-JORDAN", "sess-jordan-day1")]:
            result = await orch.handle_message(session_id, customer_id, "show me something for date night")
            print(f"\n  [{customer_id}] intent={result['intent']} confidence={result['confidence']} "
                  f"segment={result['segment']} live_model={result['used_live_model']}")
            show_results(result["results"])

        rule()
        print("\n④ — same-session follow-up, no restated context")
        followup = await orch.handle_message("sess-priya-day1", "CUST-PRIYA", "the second one, in my size")
        print(f"  [CUST-PRIYA] {followup}")

        rule()
        print("\n⑤ — size & fit, graceful no-history fallback for Jordan")
        jordan_fit = await orch.handle_message("sess-jordan-day1", "CUST-JORDAN", "does this run true to size")
        print(f"  [CUST-JORDAN] {jordan_fit}")

        rule()
        print("\n⑥ — service upsell: inside policy, then a second offer that trips it")
        inside = await orch.handle_message("sess-priya-day1", "CUST-PRIYA", "I have a styling question")
        print(f"  [CUST-PRIYA, $60 standard] committed={inside.get('committed')} order_id={inside.get('order_id')}")

        escalated = await concierge.run("CUST-PRIYA", 90.0, plan="premium")
        print(f"  [CUST-PRIYA, $90 premium]  committed={escalated.get('committed')} "
              f"escalated={escalated.get('escalated')} reason={escalated.get('reason')}")

        rule()
        print("\n⑦ — cross-session memory: new session, next day, same customer")
        day2 = await orch.handle_message("sess-priya-day2", "CUST-PRIYA", "show me something for date night")
        print(f"  [CUST-PRIYA, new session] segment={day2['segment']} — recomputed live from durable "
              f"CRM/loyalty, not re-asked")

        rule()
        print("\n⑧ — audit trail: every call above, policy-checked")
        for row in runtime.recent_audit_log(16):
            status = "OK" if row["allowed"] else "DENIED"
            print(f"  [{status:<6}] {row['caller']:<16} -> {row['tool']}")
    finally:
        await mcp_client.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
