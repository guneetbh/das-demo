"""Business outcomes (python -m neutail.reporting) — computed from the data, not asserted. A reporting view 
outside the agent mesh (read-only SQL, admin role only at the API). 
 
Conversion is a proxy: the share of (customer, category) pairs with active 
engagement (search / browse / cart — never impressions) that led to an order 
in that category. 
""" 
 
from neutail import db 
 
 
def _one(conn, sql: str, params: tuple = ()) -> float: 
    return conn.execute(sql, params).fetchone()["n"] 
 
 
def _return_rate_by_guidance(conn) -> dict: 
    guided_orders = _one(conn, """ 
        SELECT COUNT(*) AS n FROM transactional t JOIN catalogue c ON c.sku = t.sku 
        JOIN fit_profile fp ON fp.customer_id = t.customer_id AND fp.category = c.category 
        WHERE t.kind = 'order' AND t.created_at > fp.created_at""") 
    guided_returns = _one(conn, """ 
        SELECT COUNT(*) AS n FROM returns r JOIN transactional t ON t.order_id = r.order_id 
        JOIN catalogue c ON c.sku = t.sku 
        JOIN fit_profile fp ON fp.customer_id = t.customer_id AND fp.category = c.category 
        WHERE t.kind = 'order' AND t.created_at > fp.created_at""") 
    all_orders = _one(conn, "SELECT COUNT(*) AS n FROM transactional WHERE kind = 'order'") 
    all_returns = _one(conn, "SELECT COUNT(*) AS n FROM returns r JOIN transactional t ON t.order_id = r.order_id") 
    baseline_orders, baseline_returns = all_orders - guided_orders, all_returns - guided_returns 
    return { 
        "baseline_orders": baseline_orders, "baseline_returns": baseline_returns, 
        "baseline_return_rate": round(baseline_returns / baseline_orders, 4) if baseline_orders else None, 
        "guided_orders": guided_orders, "guided_returns": guided_returns, 
        "guided_return_rate": round(guided_returns / guided_orders, 4) if guided_orders else None, 
        "note": "Temporal split: an order/return counts as guided only if fit guidance for that customer and " 
                "category existed before the order was placed (returns are linked to their order).", 
    } 
 
 
def _search_to_purchase(conn) -> dict: 
    searched = _one(conn, """ 
        SELECT COUNT(DISTINCT b.customer_id || '|' || c.category) AS n 
        FROM behavioural b JOIN catalogue c ON c.sku = b.sku 
        WHERE b.event_type IN ('search','browse','cart')""") 
    converted = _one(conn, """ 
        SELECT COUNT(DISTINCT b.customer_id || '|' || c.category) AS n 
        FROM behavioural b JOIN catalogue c ON c.sku = b.sku 
        WHERE b.event_type IN ('search','browse','cart') 
          AND EXISTS (SELECT 1 FROM transactional t JOIN catalogue c2 ON c2.sku = t.sku 
                      WHERE t.customer_id = b.customer_id AND c2.category = c.category AND t.kind = 'order')""") 
    impressions = _one(conn, "SELECT COUNT(*) AS n FROM behavioural WHERE event_type = 'impression'") 
    return { 
        "searched_customer_categories": searched, "converted_customer_categories": converted, 
        "search_to_purchase_rate": round(converted / searched, 4) if searched else None, 
        "impressions_logged": impressions, 
        "note": "Proxy metric over active engagement only; recommendation impressions are excluded.", 
    } 
 
 
def _upsell(conn) -> dict: 
    return { 
        "offers_composed": _one(conn, "SELECT COUNT(*) AS n FROM offers"), 
        "offers_accepted": _one(conn, "SELECT COUNT(*) AS n FROM offers WHERE status = 'accepted'"), 
        "subscriptions_committed": _one(conn, "SELECT COUNT(*) AS n FROM transactional WHERE kind = 'subscription'"), 
        "escalations_total": _one(conn, "SELECT COUNT(*) AS n FROM escalations"), 
        "escalations_pending": _one(conn, "SELECT COUNT(*) AS n FROM escalations WHERE status = 'pending'"), 
        "escalations_approved": _one(conn, "SELECT COUNT(*) AS n FROM escalations WHERE status = 'approved'"), 
        "escalations_denied": _one(conn, "SELECT COUNT(*) AS n FROM escalations WHERE status = 'denied'"), 
    } 
 
 
def _loyalty(conn) -> dict: 
    rows = conn.execute("SELECT tier, COUNT(*) AS n FROM loyalty GROUP BY tier").fetchall() 
    return { 
        "tier_mix": {r["tier"]: r["n"] for r in rows}, 
        "points_outstanding": _one(conn, "SELECT COALESCE(SUM(points_balance), 0) AS n FROM loyalty"), 
        "points_redeemed": -_one(conn, "SELECT COALESCE(SUM(delta), 0) AS n FROM loyalty_ledger WHERE delta < 0"), 
    } 
 
 
def _population(conn) -> dict: 
    return { 
        "customers": _one(conn, "SELECT COUNT(*) AS n FROM customers"), 
        "skus": _one(conn, "SELECT COUNT(*) AS n FROM catalogue"), 
        "orders": _one(conn, "SELECT COUNT(*) AS n FROM transactional WHERE kind = 'order'"), 
        "revenue": round(_one(conn, "SELECT COALESCE(SUM(amount), 0) AS n FROM transactional"), 2), 
        "returns": _one(conn, "SELECT COUNT(*) AS n FROM returns"), 
        "audit_log_rows": _one(conn, "SELECT COUNT(*) AS n FROM audit_log"), 
    } 
 
 
def business_outcomes() -> dict: 
    with db.connection() as conn: 
        return { 
            "population": _population(conn), 
            "return_rate": _return_rate_by_guidance(conn), 
            "search_to_purchase": _search_to_purchase(conn), 
            "upsell": _upsell(conn), 
            "loyalty": _loyalty(conn), 
        } 
 
 
if __name__ == "__main__": 
    import json 
 
    print(json.dumps(business_outcomes(), indent=2)) 
