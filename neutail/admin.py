"""Admin — business outcomes (§09's headline stats, computed rather than
asserted). Read-only aggregate queries over the same tables every agent
already reads; no new agent, no new tool contract — this is a reporting
view, not part of the agent mesh.

One honesty note baked into the numbers themselves:

- "conversion" is a proxy — the share of (customer, category) search/browse
  activity that resulted in at least one order in that category, not a
  specific recommendation-impression tied to a specific purchase. There's
  no impression-level linkage in the schema yet; this is what's honestly
  computable without adding one.

The guided/baseline return-rate split *used* to carry a similar caveat —
fit_profile had no timestamp, so it could only compare "has a fit_profile
row now" vs. "doesn't," which mixes pre- and post-guidance orders into
the same "guided" bucket for any customer who ever got guidance in that
category. fit_profile now has created_at (schema.sql), set by seed.py to
the moment guidance was actually established, so this is a real temporal
split: an order counts as guided only if it happened after fit_profile
existed for that customer+category, not just if fit_profile exists today.
"""

from neutail.db import get_connection


def _one(conn, sql: str, params: tuple = ()) -> int:
    return conn.execute(sql, params).fetchone()["n"]


def _return_rate_by_guidance(conn) -> dict:
    # An order counts as "guided" only if it happened AFTER fit_profile was
    # established for that customer+category — a real before/after split,
    # not a snapshot of current state applied retroactively to past orders.
    guided_orders = _one(conn, """
        SELECT COUNT(*) AS n FROM transactional t JOIN catalogue c ON c.sku = t.sku
        JOIN fit_profile fp ON fp.customer_id = t.customer_id AND fp.category = c.category
        WHERE t.kind = 'order' AND t.created_at > fp.created_at
    """)
    guided_returns = _one(conn, """
        SELECT COUNT(*) AS n FROM returns r JOIN catalogue c ON c.sku = r.sku
        JOIN fit_profile fp ON fp.customer_id = r.customer_id AND fp.category = c.category
        WHERE r.created_at > fp.created_at
    """)
    baseline_orders = _one(conn, """
        SELECT COUNT(*) AS n FROM transactional t JOIN catalogue c ON c.sku = t.sku
        WHERE t.kind = 'order'
          AND NOT EXISTS (
              SELECT 1 FROM fit_profile fp
              WHERE fp.customer_id = t.customer_id AND fp.category = c.category AND fp.created_at < t.created_at
          )
    """)
    baseline_returns = _one(conn, """
        SELECT COUNT(*) AS n FROM returns r JOIN catalogue c ON c.sku = r.sku
        WHERE NOT EXISTS (
            SELECT 1 FROM fit_profile fp
            WHERE fp.customer_id = r.customer_id AND fp.category = c.category AND fp.created_at < r.created_at
        )
    """)
    return {
        "baseline_orders": baseline_orders,
        "baseline_returns": baseline_returns,
        "baseline_return_rate": round(baseline_returns / baseline_orders, 4) if baseline_orders else None,
        "guided_orders": guided_orders,
        "guided_returns": guided_returns,
        "guided_return_rate": round(guided_returns / guided_orders, 4) if guided_orders else None,
        "note": "temporal split — an order/return counts as guided only if fit_profile.created_at for that "
                "customer+category predates it, so a customer's earlier (pre-guidance) orders correctly land "
                "in baseline even after they later get a fit_profile row. Reflects the seed generator's own "
                "return-probability simulation (GUIDED_RETURN_RISK after guidance, TARGET_RETURN_RATE before) "
                "rather than approximating a causal effect the underlying data doesn't encode.",
    }


def _search_to_purchase_rate(conn) -> dict:
    searched = _one(conn, """
        SELECT COUNT(DISTINCT b.customer_id || '|' || c.category) AS n
        FROM behavioural b JOIN catalogue c ON c.sku = b.sku
        WHERE b.event_type IN ('search', 'browse')
    """)
    converted = _one(conn, """
        SELECT COUNT(DISTINCT b.customer_id || '|' || c.category) AS n
        FROM behavioural b JOIN catalogue c ON c.sku = b.sku
        WHERE b.event_type IN ('search', 'browse')
          AND EXISTS (
              SELECT 1 FROM transactional t JOIN catalogue c2 ON c2.sku = t.sku
              WHERE t.customer_id = b.customer_id AND c2.category = c.category AND t.kind = 'order'
          )
    """)
    return {
        "searched_customer_categories": searched,
        "converted_customer_categories": converted,
        "search_to_purchase_rate": round(converted / searched, 4) if searched else None,
        "note": "proxy metric — share of (customer, category) search/browse activity with >=1 order in that "
                "category, not a specific recommendation impression tied to a specific purchase (no "
                "impression-level linkage in the schema yet)",
    }


def _upsell_outcomes(conn) -> dict:
    committed = _one(conn, "SELECT COUNT(*) AS n FROM transactional WHERE kind = 'subscription'")
    escalated = _one(conn, "SELECT COUNT(*) AS n FROM escalations")
    approved = _one(conn, "SELECT COUNT(*) AS n FROM escalations WHERE status = 'approved'")
    denied = _one(conn, "SELECT COUNT(*) AS n FROM escalations WHERE status = 'denied'")
    pending = _one(conn, "SELECT COUNT(*) AS n FROM escalations WHERE status = 'pending'")
    return {
        "subscriptions_committed": committed,
        "escalations_total": escalated,
        "escalations_approved": approved,
        "escalations_denied": denied,
        "escalations_pending": pending,
    }


def _population_summary(conn) -> dict:
    return {
        "customers": _one(conn, "SELECT COUNT(*) AS n FROM customers"),
        "skus": _one(conn, "SELECT COUNT(*) AS n FROM catalogue"),
        "orders": _one(conn, "SELECT COUNT(*) AS n FROM transactional WHERE kind = 'order'"),
        "returns": _one(conn, "SELECT COUNT(*) AS n FROM returns"),
        "points_issued": conn.execute("SELECT COALESCE(SUM(points_balance), 0) AS n FROM loyalty").fetchone()["n"],
        "audit_log_rows": _one(conn, "SELECT COUNT(*) AS n FROM audit_log"),
    }


def business_outcomes() -> dict:
    conn = get_connection()
    try:
        return {
            "population": _population_summary(conn),
            "return_rate": _return_rate_by_guidance(conn),
            "search_to_purchase": _search_to_purchase_rate(conn),
            "upsell": _upsell_outcomes(conn),
        }
    finally:
        conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(business_outcomes(), indent=2))
