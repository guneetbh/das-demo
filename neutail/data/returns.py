from neutail import db

from neutail.data import access

FIT_REASONS = ("too_small", "too_large")


@access("returns", "R")

def for_customer_category(customer_id: str, category: str) -> list[dict]:

    with db.connection() as conn:

        rows = conn.execute(

            """SELECT r.size_returned, r.reason_code, r.created_at, r.sku FROM returns r

               JOIN catalogue c ON c.sku = r.sku

               WHERE r.customer_id = ? AND c.category = ? ORDER BY r.created_at""",

            (customer_id, category),

        ).fetchall()

    return [dict(r) for r in rows]


@access("returns", "R")

def category_return_count(category: str) -> int:

    with db.connection() as conn:

        return conn.execute(

            "SELECT COUNT(*) AS n FROM returns r JOIN catalogue c ON c.sku = r.sku WHERE c.category = ?",

            (category,),

        ).fetchone()["n"]


@access("returns", "R")

def sku_return_count(sku: str) -> int:

    with db.connection() as conn:

        return conn.execute("SELECT COUNT(*) AS n FROM returns WHERE sku = ?", (sku,)).fetchone()["n"]


@access("returns", "R")

def for_order(order_id: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM returns WHERE order_id = ?", (order_id,)).fetchone()

    return dict(row) if row else None


@access("returns", "R")

def count_for_customer(customer_id: str) -> int:

    with db.connection() as conn:

        return conn.execute("SELECT COUNT(*) AS n FROM returns WHERE customer_id = ?", (customer_id,)).fetchone()["n"]


@access("returns", "W")

def insert(*, return_id: str, order_id: str, customer_id: str, sku: str, size_returned: str,

           reason_code: str, condition_grade: str) -> None:

    with db.transaction() as conn:

        conn.execute(

            """INSERT INTO returns (return_id, order_id, customer_id, sku, size_returned, reason_code, condition_grade)

               VALUES (?, ?, ?, ?, ?, ?, ?)""",

            (return_id, order_id, customer_id, sku, size_returned, reason_code, condition_grade),

        )