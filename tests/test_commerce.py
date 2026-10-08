import threading
import uuid

import pytest

from neutail import data, db
from neutail.orchestration import orchestrator
from neutail.data import loyalty
from neutail.platform import policy, runtime

from conftest import orchestrator_ctx, shopper_ctx, staff_ctx


def _count(sql: str, *params) -> int:
    with db.connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


def _points(customer_id: str) -> int:
    with data.system_scope():
        return loyalty.get(customer_id)["points_balance"]


def test_styling_question_never_charges():
    before = _count("SELECT COUNT(*) FROM transactional WHERE customer_id = 'CUST-PRIYA'")
    reply = orchestrator.handle_message(orchestrator_ctx("CUST-PRIYA", "consent"), "I have a styling question")
    assert reply["type"] == "service" and reply["offered"] and reply["committed"] is False
    assert _count("SELECT COUNT(*) FROM transactional WHERE customer_id = 'CUST-PRIYA'") == before


def test_accept_offer_commits_once_and_is_idempotent():
    offer = orchestrator.handle_message(orchestrator_ctx("CUST-PRIYA", "accept"), "I'd like a stylist")
    key = uuid.uuid4().hex
    points_before = _points("CUST-PRIYA")
    first = runtime.invoke(shopper_ctx("CUST-PRIYA"), "accept_offer", offer_id=offer["offer_id"], idempotency_key=key)
    second = runtime.invoke(shopper_ctx("CUST-PRIYA"), "accept_offer", offer_id=offer["offer_id"], idempotency_key=key)
    assert first["committed"] and second["replayed"] and first["order_id"] == second["order_id"]
    assert _points("CUST-PRIYA") == points_before + first["points_earned"]


def test_other_customer_cannot_accept_an_offer():
    offer = orchestrator.handle_message(orchestrator_ctx("CUST-PRIYA", "steal"), "I'd like a stylist")
    with pytest.raises(ValueError):
        runtime.invoke(shopper_ctx("CUST-AMARA"), "accept_offer", offer_id=offer["offer_id"],
                       idempotency_key=uuid.uuid4().hex)


def test_no_consent_means_no_offer():
    reply = orchestrator.handle_message(orchestrator_ctx("CUST-ELENA", "consent"), "I'd like a stylist")
    assert reply["offered"] is False and "consent" in reply["reason"]


def test_order_validation_rejects_bad_quantity_and_size():
    ctx = shopper_ctx("CUST-ELENA")
    with pytest.raises(ValueError):
        runtime.invoke(ctx, "commit_order", sku="P-DRS-001", size="M", quantity=-3, idempotency_key=uuid.uuid4().hex)
    with pytest.raises(ValueError):
        runtime.invoke(ctx, "commit_order", sku="P-DRS-001", size="XXXL", idempotency_key=uuid.uuid4().hex)


def test_order_price_comes_from_catalogue_and_decrements_stock():
    stock_before = _count("SELECT SUM(stock_qty) FROM inventory WHERE sku = 'P-TOP-002' AND size = 'S'")
    out = runtime.invoke(shopper_ctx("CUST-ELENA"), "commit_order", sku="P-TOP-002", size="S",
                         idempotency_key=uuid.uuid4().hex)
    assert out["committed"] and out["amount"] == 96.0
    assert _count("SELECT SUM(stock_qty) FROM inventory WHERE sku = 'P-TOP-002' AND size = 'S'") == stock_before - 1
    assert _count("SELECT COUNT(*) FROM loyalty_ledger WHERE ref_id = ?", out["order_id"]) == 1


def test_order_replay_returns_original():
    key = uuid.uuid4().hex
    first = runtime.invoke(shopper_ctx("CUST-ELENA"), "commit_order", sku="P-TOP-002", size="M", idempotency_key=key)
    second = runtime.invoke(shopper_ctx("CUST-ELENA"), "commit_order", sku="P-TOP-002", size="M", idempotency_key=key)
    assert second["replayed"] and second["order_id"] == first["order_id"]


def test_escalation_can_only_be_resolved_once():
    offer = orchestrator.handle_message(orchestrator_ctx("CUST-AMARA", "elite"), "I'd like a stylist")
    out = runtime.invoke(shopper_ctx("CUST-AMARA"), "accept_offer", offer_id=offer["offer_id"],
                         idempotency_key=uuid.uuid4().hex)
    assert out["escalated"]
    results, errors = [], []

    def approve():
        try:
            results.append(runtime.invoke(staff_ctx("human_reviewer", "reviewer"), "resolve_escalation",
                                          escalation_id=out["escalation_id"], approve=True))
        except ValueError as exc:
            errors.append(exc)

    threads = [threading.Thread(target=approve) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 1 and len(errors) == 3
    key = f"escalation:{out['escalation_id']}"
    assert _count("SELECT COUNT(*) FROM transactional WHERE idempotency_key = ?", key) == 1


def test_only_reviewers_resolve_escalations():
    with pytest.raises(policy.PolicyDenied):
        runtime.invoke(shopper_ctx("CUST-JORDAN"), "resolve_escalation", escalation_id=1, approve=True)


def test_concurrent_points_are_not_lost():
    before = _points("CUST-KENJI")
    ctx = shopper_ctx("CUST-KENJI").as_caller("concierge_agent")

    def earn(i):
        runtime.invoke(ctx, "earn_points", amount=10, source="order", ref_id=f"concurrency-{i}")

    threads = [threading.Thread(target=earn, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert _points("CUST-KENJI") == before + 10 * round(10 * 1.25)


def test_redeem_cannot_overdraw():
    with pytest.raises(ValueError):
        runtime.invoke(shopper_ctx("CUST-JORDAN"), "redeem_points", reward_id="voucher5",
                       idempotency_key=uuid.uuid4().hex)


def test_return_feeds_tailor():
    ctx = shopper_ctx("CUST-KENJI")
    order = runtime.invoke(ctx, "commit_order", sku="V-JNS-103", size="S", idempotency_key=uuid.uuid4().hex)
    assert order["committed"]
    runtime.invoke(ctx, "record_return", order_id=order["order_id"], reason_code="too_small")
    fit = runtime.invoke(ctx, "get_fit_profile", category="jeans")
    assert fit["runs"] == "small" and fit["evidence"]["too_small"] >= 1
