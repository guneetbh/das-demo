# don't do anything

from neutail import db

from neutail.data import access


@access("inventory", "R")

def in_stock(skus: list[str]) -> set[str]:

    """One query for the whole candidate set, not one per SKU."""

    if not skus:

        return set()

    with db.connection() as conn:

        placeholders = ",".join("?" * len(skus))

        rows = conn.execute(

            f"""SELECT sku FROM inventory WHERE sku IN ({placeholders})

                GROUP BY sku HAVING SUM(stock_qty) > 0""",

            tuple(skus),

        ).fetchall()

    return {r["sku"] for r in rows}


@access("inventory", "R")

def sizes(sku: str) -> list[dict]:

    with db.connection() as conn:

        rows = conn.execute(

            "SELECT size, SUM(stock_qty) AS stock FROM inventory WHERE sku = ? GROUP BY size", (sku,)

        ).fetchall()

    order = ["XS", "S", "M", "L", "XL", "6", "7", "8", "9", "10", "ONE"]

    out = [dict(r) for r in rows]

    out.sort(key=lambda r: order.index(r["size"]) if r["size"] in order else 99)

    return out


@access("inventory", "R")

def stock_level(skus: list[str]) -> dict[str, int]:

    if not skus:

        return {}

    with db.connection() as conn:

        placeholders = ",".join("?" * len(skus))

        rows = conn.execute(

            f"SELECT sku, SUM(stock_qty) AS total FROM inventory WHERE sku IN ({placeholders}) GROUP BY sku",

            tuple(skus),

        ).fetchall()

    return {r["sku"]: r["total"] for r in rows}


@access("inventory", "W")

def decrement(sku: str, size: str, quantity: int) -> None:

    with db.transaction() as conn:

        row = conn.execute(

            """SELECT location FROM inventory WHERE sku = ? AND size = ? AND stock_qty >= ?

               ORDER BY stock_qty DESC LIMIT 1""",

            (sku, size, quantity),

        ).fetchone()

        if row is None:

            raise ValueError(f"{sku} size {size} is out of stock")

        conn.execute(

            "UPDATE inventory SET stock_qty = stock_qty - ? WHERE sku = ? AND size = ? AND location = ?",

            (quantity, sku, size, row["location"]),

        )