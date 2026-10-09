import time

from neutail import db
from neutail.data import access


_effect_cache: tuple[float, float] | None = None


@access("fit", "R")
def guidance_effect(ttl_seconds: int = 600) -> float:
    """Population-level ratio of return rate after fit guidance existed vs.
    before (temporal split) — how much following guidance has cut returns in
    the estate's own history. Cached; it moves slowly."""
    global _effect_cache
    now = time.monotonic()
    if _effect_cache and now - _effect_cache[0] < ttl_seconds:
        return _effect_cache[1]
    with db.connection() as conn:
        def one(sql: str) -> int:
            return conn.execute(sql).fetchone()["n"]

        guided_orders = one("""SELECT COUNT(*) AS n FROM transactional t JOIN catalogue c ON c.sku = t.sku
                               JOIN fit_profile fp ON fp.customer_id = t.customer_id AND fp.category = c.category
                               WHERE t.kind = 'order' AND t.created_at > fp.created_at""")
        guided_returns = one("""SELECT COUNT(*) AS n FROM returns r JOIN transactional t ON t.order_id = r.order_id
                                JOIN catalogue c ON c.sku = t.sku
                                JOIN fit_profile fp ON fp.customer_id = t.customer_id AND fp.category = c.category
                                WHERE t.kind = 'order' AND t.created_at > fp.created_at""")
        all_orders = one("SELECT COUNT(*) AS n FROM transactional WHERE kind = 'order'")
        all_returns = one("SELECT COUNT(*) AS n FROM returns r JOIN transactional t ON t.order_id = r.order_id")
    base_orders, base_returns = all_orders - guided_orders, all_returns - guided_returns
    if guided_orders < 20 or base_orders < 20 or base_returns == 0:
        effect = 0.4  # not enough history to measure; conservative prior
    else:
        effect = (guided_returns / guided_orders) / (base_returns / base_orders)
    effect = round(min(1.0, max(0.15, effect)), 3)
    _effect_cache = (now, effect)
    return effect


@access("fit", "R")
def get_profile(customer_id: str, category: str) -> dict | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM fit_profile WHERE customer_id = ? AND category = ?", (customer_id, category)
        ).fetchone()
    return dict(row) if row else None
