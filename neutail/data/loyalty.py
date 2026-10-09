import sqlite3

from neutail import db

from neutail.data import access


@access("loyalty", "R")

def get(customer_id: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM loyalty WHERE customer_id = ?", (customer_id,)).fetchone()

    return dict(row) if row else None


@access("loyalty", "R")

def ledger(customer_id: str, limit: int = 20) -> list[dict]:

    with db.connection() as conn:

        rows = conn.execute(

            "SELECT * FROM loyalty_ledger WHERE customer_id = ? ORDER BY entry_id DESC LIMIT ?",

            (customer_id, limit),

        ).fetchall()

    return [dict(r) for r in rows]


@access("loyalty", "R")

def ledger_entry(source: str, ref_id: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute(

            "SELECT * FROM loyalty_ledger WHERE source = ? AND ref_id = ?", (source, ref_id)

        ).fetchone()

    return dict(row) if row else None


@access("loyalty", "RW")

def apply_entry(customer_id: str, delta: int, spend_delta: float, source: str, ref_id: str, note: str = "") -> bool:

    """Ledger insert + balance update as one atomic step. Returns False (and

    changes nothing) if this (source, ref_id) was already applied."""

    with db.transaction() as conn:

        try:

            conn.execute(

                "INSERT INTO loyalty_ledger (customer_id, delta, source, ref_id, note) VALUES (?, ?, ?, ?, ?)",

                (customer_id, delta, source, ref_id, note),

            )

        except sqlite3.IntegrityError:

            return False

        cur = conn.execute(

            """UPDATE loyalty SET points_balance = points_balance + ?, ytd_spend = ytd_spend + ?

               WHERE customer_id = ? AND points_balance + ? >= 0""",

            (delta, spend_delta, customer_id, delta),

        )

        if cur.rowcount != 1:

            raise ValueError("insufficient points balance or no loyalty record")

    return True


@access("loyalty", "W")

def set_tier(customer_id: str, tier: str) -> None:

    with db.transaction() as conn:

        conn.execute("UPDATE loyalty SET tier = ? WHERE customer_id = ?", (tier, customer_id))