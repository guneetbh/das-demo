import pytest

from neutail import data
from neutail.data import loyalty
from neutail.platform import contracts, policy, runtime

from conftest import shopper_ctx, staff_ctx


def test_storefront_cannot_award_itself_points():
    with pytest.raises(policy.PolicyDenied):
         runtime.invoke(shopper_ctx("CUST-JORDAN"), "earn_points", amount=1000, source="order", ref_id="forged-1")

def test_shopper_session_cannot_read_another_customer():
    with pytest.raises(policy.PolicyDenied):
        runtime.invoke(shopper_ctx("CUST-JORDAN"), "get_loyalty_status", customer_id="CUST-PRIYA")

def test_external_mcp_is_read_only():
    ctx = staff_ctx("external_mcp", "external")
    assert runtime.invoke(ctx, "get_loyalty_status", customer_id="CUST-PRIYA")["tier"]
    with pytest.raises(policy.PolicyDenied):
        runtime.invoke(ctx, "commit_order", customer_id="CUST-PRIYA", sku="P-DRS-001", size="M",
                        idempotency_key="external-attempt-1")
    with pytest.raises(policy.PolicyDenied):
        runtime.invoke(ctx, "rank_products", customer_id="CUST-PRIYA", query="jeans")


def test_staff_principal_must_name_a_customer():
    with pytest.raises(ValueError):
        runtime.invoke(staff_ctx("admin", "admin"), "get_loyalty_status")

def test_contract_cannot_claim_domain_outside_matrix():
    with pytest.raises(ValueError, match="not granted"):
        contracts.register("sneaky_tool", owner="muse_agent", allowed_callers=["admin"],
                            handler=lambda ctx: {}, domains={"loyalty": "W"})

def test_data_layer_rejects_calls_outside_a_scope():
    with pytest.raises(data.DataAccessDenied):
        loyalty.get("CUST-PRIYA")


def test_data_layer_rejects_undeclared_domain_inside_a_tool():
    with data.scope("muse_agent", {"catalogue": "R"}):
        with pytest.raises(data.DataAccessDenied):
            loyalty.get("CUST-PRIYA")

def test_side_effect_rate_limit(monkeypatch):
    monkeypatch.setattr(policy.config, "SIDE_EFFECT_RATE_LIMIT_PER_MINUTE", 2)
    ctx = shopper_ctx("CUST-KENJI")
    for _ in range(2):
        with pytest.raises(ValueError): # passes the limiter, fails validation
            runtime.invoke(ctx, "commit_order", sku="P-DRS-001", size="M", quantity=0, idempotency_key="rl-test-key")
    with pytest.raises(policy.RateLimited):
        runtime.invoke(ctx, "commit_order", sku="P-DRS-001", size="M", idempotency_key="rl-test-key")