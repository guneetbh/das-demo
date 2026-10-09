from neutail import db

from neutail.data import access

CATEGORIES = ("dress", "top", "jeans", "shoes", "accessories", "jacket", "athleisure")


@access("catalogue", "R")

def get(sku: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM catalogue WHERE sku = ?", (sku,)).fetchone()

    return dict(row) if row else None


@access("catalogue", "R")

def require(sku: str) -> dict:

    item = get(sku)

    if item is None:

        raise ValueError(f"unknown sku: {sku}")

    return item


@access("catalogue", "R")

def many(skus: list[str]) -> dict[str, dict]:

    if not skus:

        return {}

    with db.connection() as conn:

        placeholders = ",".join("?" * len(skus))

        rows = conn.execute(f"SELECT * FROM catalogue WHERE sku IN ({placeholders})", tuple(skus)).fetchall()

    return {r["sku"]: dict(r) for r in rows}


@access("catalogue", "R")

def browse(category: str | None = None, *, trending: bool = False, on_sale: bool = False,

           new_only: bool = False, tier: str | None = None, limit: int = 300) -> list[dict]:

    clauses, params = [], []

    if category:

        clauses.append("category = ?")

        params.append(category)

    if trending:

        clauses.append("trending = 1")

    if on_sale:

        clauses.append("was_price IS NOT NULL AND was_price > price")

    if new_only:

        clauses.append("is_new = 1")

    if tier:

        clauses.append("tier = ?")

        params.append(tier)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with db.connection() as conn:

        rows = conn.execute(

            f"SELECT * FROM catalogue {where} ORDER BY trending DESC, sku LIMIT ?", (*params, limit)

        ).fetchall()

    return [dict(r) for r in rows]


@access("catalogue", "R")

def all_for_index() -> list[dict]:

    with db.connection() as conn:

        rows = conn.execute(

            "SELECT sku, name, category, occasion_tags, colour, material, description, tier, is_new, updated_at "

            "FROM catalogue ORDER BY sku"

        ).fetchall()

    return [dict(r) for r in rows]


@access("catalogue", "R")

def get_plan(plan: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM plans WHERE plan = ?", (plan,)).fetchone()

    return dict(row) if row else None


@access("catalogue", "R")

def plans() -> list[dict]:

    with db.connection() as conn:

        rows = conn.execute("SELECT * FROM plans ORDER BY monthly_price").fetchall()

    return [dict(r) for r in rows]