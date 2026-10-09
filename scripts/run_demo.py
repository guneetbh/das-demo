"""Walks the demo script end to end in-process (no servers needed).

    python -m neutail.seed && python scripts/run_demo.py

Each step builds the same Context the API would (shopper-bound, fresh
trace_id) and calls the orchestrator or runtime — the exact production path.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # works without `pip install -e .` too

from neutail import agents  # noqa: E402,F401 — registers contracts
from neutail.orchestration import orchestrator  # noqa: E402
from neutail.context import Context  # noqa: E402
from neutail.platform import audit, policy, runtime  # noqa: E402


def shopper(customer_id: str, session_id: str) -> Context:
    ctx = Context(principal=orchestrator.ORCHESTRATOR, customer_id=customer_id, session_id=session_id,
                  actor=f"shopper:{audit.hash_customer(customer_id)}", role="shopper")
    audit.current_trace.set(ctx.trace_id)
    return ctx


def storefront(customer_id: str) -> Context:
    return shopper(customer_id, "demo").as_caller("storefront")


def rule(title: str) -> None:
    print("\n" + "-" * 78 + f"\n{title}")


def show(results: list[dict]) -> None:
    for r in results:
        print(f"    {r['sku']:<11} {r['name'][:30]:<30} {r['tier']:<13} £{r['price']:>7.2f}  "
              f"risk={r['return_risk']:.0%}  {r['reason'][:60]}")


def main() -> None:
    # Windows consoles default to cp1252, which can't print £, ① or arrows.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("NEU.TAIL — DEMO WALKTHROUGH")

    rule("① ② ③  orchestration, profiling, discovery — same query, two personas")
    for cid in ("CUST-PRIYA", "CUST-JORDAN"):
        r = orchestrator.handle_message(shopper(cid, f"{cid}-d1"), "show me something for date night")
        print(f"\n  [{cid}] segment={r['segment']}/{r['micro_segment']} live_rank={r['used_live_model']}")
        show(r["results"][:4])

    rule("④  same-session follow-up, no restated context")
    r = orchestrator.handle_message(shopper("CUST-PRIYA", "CUST-PRIYA-d1"), "the second one, in my size")
    print(f"  {r['type']}: {r.get('category')} → {r.get('guidance')} (risk {r.get('return_risk', 0):.0%})")

    rule("⑤  size & fit from GRADE's return reasons — Priya's jeans, Jordan with no history")
    for cid in ("CUST-PRIYA", "CUST-JORDAN"):
        r = orchestrator.handle_message(shopper(cid, f"{cid}-d1"), "check my fit for jeans")
        print(f"  [{cid}] {r['guidance']} · risk {r['return_risk']:.0%} vs baseline {r['baseline_return_risk']:.0%}")

    rule("⑥  upsell needs consent: offer → explicit accept → SENTRY → one transaction")
    ctx = shopper("CUST-PRIYA", "CUST-PRIYA-d1")
    offer = orchestrator.handle_message(ctx, "I'd like a stylist")
    print(f"  offer: {offer['plan_name']} £{offer['amount']:.2f}/mo · committed={offer['committed']} — nothing charged yet")
    accepted = orchestrator.handle_message(shopper("CUST-PRIYA", "CUST-PRIYA-d1"), "yes, sign me up")
    print(f"  accept: committed={accepted['committed']} order={accepted.get('order_id')} +{accepted.get('points_earned')} pts")
    elite = orchestrator.handle_message(shopper("CUST-AMARA", "CUST-AMARA-d1"), "I'd like a stylist")
    out = runtime.invoke(storefront("CUST-AMARA"), "accept_offer", offer_id=elite["offer_id"],
                         idempotency_key=uuid.uuid4().hex)
    print(f"  Amara {elite['plan_name']} £{elite['amount']:.2f}: escalated={out['escalated']} — {out.get('reason')}")
    elena = orchestrator.handle_message(shopper("CUST-ELENA", "CUST-ELENA-d1"), "I'd like a stylist")
    print(f"  Elena: offered={elena['offered']} — {elena.get('reason')}")

    rule("⑦  purchase with idempotency, and a tier upgrade (Sam is £150 from Gold)")
    key = uuid.uuid4().hex
    first = runtime.invoke(storefront("CUST-SAM"), "commit_order", sku="P-JKT-007", size="M", idempotency_key=key)
    again = runtime.invoke(storefront("CUST-SAM"), "commit_order", sku="P-JKT-007", size="M", idempotency_key=key)
    print(f"  order {first['order_id']} £{first['amount']:.2f} +{first['points_earned']} pts · "
          f"upgraded to {first['tier_upgraded_to']} · retry returned same order: {again['order_id'] == first['order_id']}")

    rule("⑧  cross-session memory: new session, segment recomputed from durable data")
    r = orchestrator.handle_message(shopper("CUST-PRIYA", "CUST-PRIYA-d2"), "new arrivals")
    print(f"  segment={r['segment']}/{r['micro_segment']}, {len(r['results'])} results, no context re-asked")

    rule("⑨  policy: identity comes from the runtime, not the request")
    try:
        runtime.invoke(storefront("CUST-JORDAN"), "earn_points", amount=500, source="order", ref_id="forged")
    except policy.PolicyDenied as denied:
        print(f"  storefront → earn_points: DENIED ({denied.reason})")
    try:
        runtime.invoke(storefront("CUST-JORDAN"), "get_loyalty_status", customer_id="CUST-PRIYA")
    except policy.PolicyDenied as denied:
        print(f"  Jordan's session asking for Priya's loyalty: DENIED ({denied.reason})")

    rule("⑩  audit trail for step ⑧'s trace")
    for row in audit.recent(trace_id=r["trace_id"]):
        print(f"  [{row['outcome']:<8}] {row['kind']:<9} {row['caller']:<18} → {row['tool']:<26} {row['latency_ms'] or 0:>4} ms")


if __name__ == "__main__":
    main()
