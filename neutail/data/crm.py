# don't do anything

from neutail import db

from neutail.data import access


@access("crm", "R")

def get_customer(customer_id: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM customers WHERE customer_id = ?", (customer_id,)).fetchone()

    return dict(row) if row else None


@access("crm", "R")

def require_customer(customer_id: str) -> dict:

    customer = get_customer(customer_id)

    if customer is None:

        raise ValueError(f"unknown customer_id: {customer_id}")

    return customer


@access("crm", "R")

def find_by_email(email: str) -> dict | None:

    with db.connection() as conn:

        row = conn.execute("SELECT * FROM customers WHERE lower(email) = lower(?)", (email,)).fetchone()

    return dict(row) if row else None