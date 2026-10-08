from neutail import db

from neutail.data import access

ENGAGEMENT_EVENTS = ("browse", "search", "cart")


@access("behavioural", "W")

def log(customer_id: str, event_type: str, skus: list[str | None], query: str | None = None) -> None:

    if not skus:

        return

    with db.transaction() as conn:

        conn.executemany(

            "INSERT INTO behavioural (customer_id, event_type, sku, query) VALUES (?, ?, ?, ?)",

            [(customer_id, event_type, sku, query) for sku in skus],

        )


@access("behavioural", "R")

def engaged_categories(customer_id: str, hours: int) -> set[str]:

    """Categories the customer actively engaged with — impressions excluded,

    so recommendations can't reinforce themselves."""

    with db.connection() as conn:

        rows = conn.execute(

            f"""SELECT DISTINCT c.category FROM behavioural b JOIN catalogue c ON c.sku = b.sku

                WHERE b.customer_id = ? AND b.event_type IN ({",".join("?" * len(ENGAGEMENT_EVENTS))})

                  AND b.created_at >= datetime('now', ?)""",

            (customer_id, *ENGAGEMENT_EVENTS, f"-{hours} hours"),

        ).fetchall()

    return {r["category"] for r in rows}


@access("behavioural", "R")

def category_affinity(customer_id: str, days: int = 90) -> dict[str, int]:

    with db.connection() as conn:

        rows = conn.execute(

            f"""SELECT c.category, COUNT(*) AS n FROM behavioural b JOIN catalogue c ON c.sku = b.sku

                WHERE b.customer_id = ? AND b.event_type IN ({",".join("?" * len(ENGAGEMENT_EVENTS))})

                  AND b.created_at >= datetime('now', ?)

                GROUP BY c.category ORDER BY n DESC""",

            (customer_id, *ENGAGEMENT_EVENTS, f"-{days} days"),

        ).fetchall()

    return {r["category"]: r["n"] for r in rows}


@access("behavioural", "R")

def last_activity(customer_id: str) -> str | None:

    with db.connection() as conn:

        row = conn.execute(

            f"""SELECT MAX(created_at) AS t FROM behavioural

                WHERE customer_id = ? AND event_type IN ({",".join("?" * len(ENGAGEMENT_EVENTS))})""",

            (customer_id, *ENGAGEMENT_EVENTS),

        ).fetchone()

    return row["t"]


@access("behavioural", "R")

def recently_viewed(customer_id: str, limit: int = 8) -> list[str]:

    with db.connection() as conn:

        rows = conn.execute(

            """SELECT sku, MAX(created_at) AS t FROM behavioural

               WHERE customer_id = ? AND event_type = 'browse' AND sku IS NOT NULL

               GROUP BY sku ORDER BY t DESC LIMIT ?""",

            (customer_id, limit),

        ).fetchall()

    return [r["sku"] for r in rows]


@access("behavioural", "R")

def event_count(customer_id: str, event_type: str) -> int:

    with db.connection() as conn:

        return conn.execute(

            "SELECT COUNT(*) AS n FROM behavioural WHERE customer_id = ? AND event_type = ?",

            (customer_id, event_type),

        ).fetchone()["n"]


@access("behavioural", "R")

def active_weeks(customer_id: str, weeks: int = 8) -> int:

    with db.connection() as conn:

        return conn.execute(

            """SELECT COUNT(DISTINCT strftime('%Y-%W', created_at)) AS n FROM behavioural

               WHERE customer_id = ? AND event_type IN ('browse','search','cart')

                 AND created_at >= datetime('now', ?)""",

            (customer_id, f"-{weeks * 7} days"),

        ).fetchone()["n"]


@access("behavioural", "R")

def category_momentum(days: int = 7) -> dict[str, int]:

    """Estate-wide engagement per category over the window — SCOUT's live signal."""

    with db.connection() as conn:

        rows = conn.execute(

            f"""SELECT c.category, COUNT(*) AS n FROM behavioural b JOIN catalogue c ON c.sku = b.sku

                WHERE b.event_type IN ({",".join("?" * len(ENGAGEMENT_EVENTS))})

                  AND b.created_at >= datetime('now', ?)

                GROUP BY c.category""",

            (*ENGAGEMENT_EVENTS, f"-{days} days"),

        ).fetchall()

    return {r["category"]: r["n"] for r in rows}