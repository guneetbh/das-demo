"""Seeds the Data Universe with the two demo customers (§06) and a date-night catalogue.

Run directly: python -m neutail.seed
"""

from neutail.db import get_connection, init_schema

CUSTOMERS = [
    ("CUST-PRIYA", "Priya Nair", "priya@example.com", 18),
    ("CUST-JORDAN", "Jordan Lee", "jordan@example.com", 2),
]

LOYALTY = [
    ("CUST-PRIYA", "Gold", 4200, 3100.0),
    ("CUST-JORDAN", "Bronze", 120, 180.0),
]

# sku, name, category, price, tier, trending, occasion_tags
CATALOGUE = [
    ("P-DRS-001", "Silk Wrap Dress", "dress", 228.0, "premium", 0, "date-night,evening"),
    ("P-TOP-002", "Satin Cami Top", "top", 96.0, "premium", 0, "date-night"),
    ("P-JNS-003", "Tailored Dark Denim", "jeans", 148.0, "premium", 0, "date-night,everyday"),
    ("P-SHO-004", "Block Heel Sandal", "shoes", 189.0, "premium", 0, "date-night,evening"),
    ("P-ACC-005", "Statement Drop Earrings", "accessories", 68.0, "premium", 0, "date-night,evening"),
    ("P-DRS-006", "Velvet Slip Dress", "dress", 255.0, "premium", 1, "date-night,evening"),
    ("P-JKT-007", "Cropped Blazer", "jacket", 214.0, "premium", 0, "date-night,work"),
    ("P-ATH-008", "Performance Legging", "athleisure", 84.0, "premium", 0, "everyday,gym"),
    ("V-DRS-101", "Wrap Midi Dress", "dress", 58.0, "private_label", 0, "date-night,everyday"),
    ("V-TOP-102", "Satin-Feel Cami", "top", 32.0, "private_label", 0, "date-night"),
    ("V-JNS-103", "Skinny Dark Wash Jeans", "jeans", 44.0, "private_label", 0, "date-night,everyday"),
    ("V-SHO-104", "Strappy Block Heel", "shoes", 52.0, "private_label", 0, "date-night,evening"),
    ("V-ACC-105", "Drop Earrings", "accessories", 18.0, "private_label", 0, "date-night"),
    ("V-DRS-106", "Slip Dress", "dress", 49.0, "private_label", 1, "date-night,evening"),
    ("V-JKT-107", "Cropped Jacket", "jacket", 56.0, "private_label", 0, "date-night,work"),
    ("V-ATH-108", "Basic Legging", "athleisure", 22.0, "private_label", 0, "everyday,gym"),
]

APPAREL_SIZES = ["XS", "S", "M", "L", "XL"]
SHOE_SIZES = ["6", "7", "8", "9", "10"]

FIT_PROFILE = [
    # customer_id, category, preferred_size, runs
    ("CUST-PRIYA", "jeans", "M", "small"),
]

RETURNS = [
    # return_id, customer_id, sku, size_returned, reason_code
    ("RET-0001", "CUST-PRIYA", "P-JNS-003", "M", "too_small"),
]

BEHAVIOURAL = [
    # customer_id, event_type, sku
    ("CUST-PRIYA", "browse", "P-DRS-006"),
    ("CUST-PRIYA", "search", None),
    ("CUST-JORDAN", "browse", "V-DRS-101"),
    ("CUST-JORDAN", "browse", "V-DRS-106"),
]


def seed() -> None:
    init_schema()
    conn = get_connection()
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO customers (customer_id, name, email, tenure_months) VALUES (?, ?, ?, ?)",
            CUSTOMERS,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO loyalty (customer_id, tier, points_balance, ytd_spend) VALUES (?, ?, ?, ?)",
            LOYALTY,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO catalogue
               (sku, name, category, price, tier, trending, occasion_tags)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            CATALOGUE,
        )

        inventory_rows = []
        for sku, _name, category, *_rest in CATALOGUE:
            sizes = SHOE_SIZES if category == "shoes" else APPAREL_SIZES
            for size in sizes:
                inventory_rows.append((sku, size, "DC1", 12))
        conn.executemany(
            "INSERT OR IGNORE INTO inventory (sku, size, location, stock_qty) VALUES (?, ?, ?, ?)",
            inventory_rows,
        )

        conn.executemany(
            "INSERT OR IGNORE INTO fit_profile (customer_id, category, preferred_size, runs) VALUES (?, ?, ?, ?)",
            FIT_PROFILE,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO returns (return_id, customer_id, sku, size_returned, reason_code)
               VALUES (?, ?, ?, ?, ?)""",
            RETURNS,
        )
        conn.executemany(
            "INSERT INTO behavioural (customer_id, event_type, sku) VALUES (?, ?, ?)",
            BEHAVIOURAL,
        )
    conn.close()
    print(f"Seeded {len(CUSTOMERS)} customers, {len(CATALOGUE)} SKUs, {len(inventory_rows)} inventory rows.")


if __name__ == "__main__":
    seed()
