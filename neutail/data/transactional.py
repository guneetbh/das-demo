from neutail import db

from neutail.data import access


@access("transactional", "R")

def by_idempotency_key(key: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM transactional WHERE idempotency_key = ?", (key,)).fetchone()

    return dict(row) if row else None


@access("transactional", "R")

def get_order(order_id: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM transactional WHERE order_id = ?", (order_id,)).fetchone()

    return dict(row) if row else None


@access("transactional", "W")

def insert_order(*, order_id: str, customer_id: str, kind: str, amount: float, sku: str | None = None,

                 size: str | None = None, quantity: int = 1, plan: str | None = None,

                 idempotency_key: str | None = None) -> None:

    with db.transaction() as conn:

        conn.execute(

            """INSERT INTO transactional (order_id, customer_id, sku, size, quantity, kind, plan, amount, idempotency_key)

               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",

            (order_id, customer_id, sku, size, quantity, kind, plan, amount, idempotency_key),

        )


@access("transactional", "R")

def history(customer_id: str, limit: int = 50) -> list[dict]:

    with db.connection() as conn:

        rows = conn.execute(

            """SELECT t.*, c.name AS product_name, c.category, c.image_url,

                      (SELECT r.reason_code FROM returns r WHERE r.order_id = t.order_id LIMIT 1) AS returned_reason

               FROM transactional t LEFT JOIN catalogue c ON c.sku = t.sku

               WHERE t.customer_id = ? ORDER BY t.created_at DESC LIMIT ?""",

            (customer_id, limit),

        ).fetchall()

    return [dict(r) for r in rows]


@access("transactional", "R")

def stats(customer_id: str) -> dict:

    with db.connection() as conn:

        row = conn.execute(

            """SELECT COUNT(*) AS n_orders,

                      COALESCE(AVG(amount), 0) AS avg_amount,

                      COALESCE(SUM(CASE WHEN created_at >= datetime('now','-90 days') THEN amount END), 0) AS spend_90d,

                      COALESCE(SUM(CASE WHEN created_at >= datetime('now','-1 day') THEN 1 END), 0) AS n_last_24h,

                      MAX(created_at) AS last_order_at

               FROM transactional WHERE customer_id = ?""",

            (customer_id,),

        ).fetchone()

    return dict(row)


@access("transactional", "R")

def category_counts(customer_id: str) -> dict[str, int]:

    with db.connection() as conn:

        rows = conn.execute(

            """SELECT c.category, COUNT(*) AS n FROM transactional t JOIN catalogue c ON c.sku = t.sku

               WHERE t.customer_id = ? AND t.kind = 'order' GROUP BY c.category""",

            (customer_id,),

        ).fetchall()

    return {r["category"]: r["n"] for r in rows}


@access("transactional", "R")

def orders_in_category(customer_id: str, category: str) -> list[dict]:

    with db.connection() as conn:

        rows = conn.execute(

            """SELECT t.order_id, t.sku, t.size, t.created_at FROM transactional t

               JOIN catalogue c ON c.sku = t.sku

               WHERE t.customer_id = ? AND c.category = ? AND t.kind = 'order'""",

            (customer_id, category),

        ).fetchall()

    return [dict(r) for r in rows]


@access("transactional", "R")

def category_order_count(category: str) -> int:

    with db.connection() as conn:

        return conn.execute(

            """SELECT COUNT(*) AS n FROM transactional t JOIN catalogue c ON c.sku = t.sku

               WHERE t.kind = 'order' AND c.category = ?""",

            (category,),

        ).fetchone()["n"]


@access("transactional", "R")

def sku_order_count(sku: str) -> int:

    with db.connection() as conn:

        return conn.execute(

            "SELECT COUNT(*) AS n FROM transactional WHERE sku = ? AND kind = 'order'", (sku,)

        ).fetchone()["n"]


@access("transactional", "R")

def offer(offer_id: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM offers WHERE offer_id = ?", (offer_id,)).fetchone()

    return dict(row) if row else None


@access("transactional", "W")

def insert_offer(*, offer_id: str, customer_id: str, plan: str, amount: float, offer_text: str,

                 ttl_minutes: int = 30) -> None:

    with db.transaction() as conn:

        conn.execute(

            """INSERT INTO offers (offer_id, customer_id, plan, amount, offer_text, expires_at)

               VALUES (?, ?, ?, ?, ?, datetime('now', ?))""",

            (offer_id, customer_id, plan, amount, offer_text, f"+{ttl_minutes} minutes"),

        )


@access("transactional", "W")

def transition_offer(offer_id: str, from_status: str, to_status: str, order_id: str | None = None) -> bool:

    """Compare-and-set: only one caller can move an offer out of a given state."""

    with db.transaction() as conn:

        cur = conn.execute(

            "UPDATE offers SET status = ?, order_id = COALESCE(?, order_id) WHERE offer_id = ? AND status = ?",

            (to_status, order_id, offer_id, from_status),

        )

    return cur.rowcount == 1


@access("transactional", "R")

def offer_expired(offer_id: str) -> bool:

    with db.connection() as conn:

        row = conn.execute(

            "SELECT expires_at < datetime('now') AS expired FROM offers WHERE offer_id = ?", (offer_id,)

        ).fetchone()

    return bool(row and row["expired"])