import json

from fastapi.testclient import TestClient

from neutail import db
from neutail.orchestration import intents, orchestrator
from neutail.agents.muse import apply_diversity_cap
from neutail.api import app

from conftest import orchestrator_ctx

client = TestClient(app)


def test_keyword_fallback_intents_and_entities():
    assert [i.intent for i in intents.classify_keywords("jeans")] == ["discovery"]
    assert intents.classify_keywords("how many points do I have")[0].intent == "loyalty"
    multi = intents.classify_keywords("show me something for date night, and check my fit for jeans")
    assert [(i.intent, i.category) for i in multi] == [("discovery", None), ("fit", "jeans")]
    assert intents.classify_keywords("find me an outfit")[0].intent == "discovery"  # 'outfit' is not 'fit'
    assert intents.extract_ordinal("first-time buyer") is None
    assert intents.extract_ordinal("the 3rd one") == 3


def test_multi_intent_sizes_the_named_category():
    reply = orchestrator.handle_message(orchestrator_ctx("CUST-PRIYA", "multi"),
                                        "show me something for date night, and check my fit for jeans")
    assert reply["type"] == "composite"
    fit = next(p for p in reply["parts"] if p["type"] == "fit")
    assert fit["category"] == "jeans"


def test_follow_up_memory_is_scoped_to_customer():
    orchestrator.handle_message(orchestrator_ctx("CUST-PRIYA", "shared-id"), "show me dresses")
    other = orchestrator.handle_message(orchestrator_ctx("CUST-JORDAN", "shared-id"), "the second one, in my size")
    assert other["type"] == "clarify"  # Jordan must not inherit Priya's results


def _engaged(cid: str) -> int:
    with db.connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM behavioural WHERE customer_id = ? AND event_type != 'impression'",
                            (cid,)).fetchone()[0]


def test_recommendations_log_impressions_not_engagement():
    before = _engaged("CUST-KENJI")
    reply = orchestrator.recommend(orchestrator_ctx("CUST-KENJI", "reco"))
    assert reply["results"]
    assert _engaged("CUST-KENJI") == before


def test_diversity_cap():
    pool = [{"sku": str(i), "category": "dress" if i < 6 else "top"} for i in range(8)]
    picked = apply_diversity_cap(pool, 4)
    assert [p["category"] for p in picked].count("dress") == 2


def test_api_requires_auth_and_ignores_body_identity():
    assert client.post("/chat", json={"session_id": "x", "message": "jeans"}).status_code == 401
    token = client.post("/auth/login", json={"handle": "CUST-JORDAN"}).json()["token"]
    forged = client.post("/tools/earn_points/invoke", headers={"X-API-Key": "dev-mcp-key"},
                         json={"args": {"customer_id": "CUST-JORDAN", "amount": 9999, "source": "order",
                                        "ref_id": "x123", "caller": "concierge_agent"}})
    assert forged.status_code == 403
    assert client.get("/audit", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert client.get("/audit", headers={"X-API-Key": "dev-reviewer-key"}).status_code == 403


def test_trace_links_every_hop_and_audit_is_redacted():
    token = client.post("/auth/login", json={"handle": "CUST-PRIYA"}).json()["token"]
    reply = client.post("/chat", headers={"Authorization": f"Bearer {token}"},
                        json={"session_id": "trace", "message": "show me blazers"}).json()
    rows = client.get(f"/admin/traces/{reply['trace_id']}", headers={"X-API-Key": "dev-admin-key"}).json()["entries"]
    tools = {r["tool"] for r in rows}
    assert {"get_customer_segment", "rank_products", "get_fit_profile", "vector_store:search"} <= tools
    blob = json.dumps(rows)
    assert "CUST-PRIYA" not in blob and "priya.nair@example.com" not in blob


def test_every_product_has_a_real_image():
    with db.connection() as conn:
        urls = {r[0] for r in conn.execute("SELECT DISTINCT image_url FROM catalogue")}
    from neutail import config
    missing = [u for u in urls if not (config.ROOT / u.lstrip("/")).exists()]
    assert urls and not missing
