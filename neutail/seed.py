"""Seeds the Data Universe (§04) at build-requirement scale: 30-50
customers spread across segments, 1,000+ SKUs, 12 months of transactions,
and a returns history that approximately reproduces a 34% baseline —
dropping to TAILOR's own GUIDED_RETURN_RISK once fit guidance exists for
that customer+category, simulated causally (see
_generate_transactions_and_returns), not just labeled after the fact.
Run `python -m neutail.admin` — or GET /admin/outcomes — to see the
resulting baseline-vs-guided split computed from what actually got
generated.

Two hand-authored "hero" customers (Priya, Jordan) and 16 hand-authored
catalogue items carry the exact §06 demo script — they're untouched by
the generator below, so nothing about the existing demo changes. The
requirement's scale target is met by a *separate* generated population
layered on top, with a fixed RNG seed so `python -m neutail.seed` always
produces the same database (that's what "repeatable" means here — the
generator is deterministic, not that calendar dates are frozen).

Run directly: python -m neutail.seed
"""

import random
from datetime import datetime, timedelta

from neutail import vector_store
from neutail.agents.tally import TIER_MULTIPLIER  # reuse — don't duplicate the points formula
from neutail.agents.tailor import GUIDED_RETURN_RISK  # reuse — the seed generator and TAILOR agree on what "guided" means
from neutail.db import get_connection, init_schema

# ---------------------------------------------------------------------------
# Hero data — exactly as before. The §06 demo script depends on these values.
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Bulk generation — the requirement's scale target, fixed-seed and deterministic.
# ---------------------------------------------------------------------------

RNG_SEED = 20260101
TARGET_CUSTOMERS = 38  # + the 2 heroes = 40, inside the 30-50 range
TARGET_RETURN_RATE = 0.34
MONTHS_OF_HISTORY = 12

FIRST_NAMES = [
    "Alex", "Jordan", "Sam", "Taylor", "Morgan", "Casey", "Riley", "Jamie",
    "Avery", "Priya", "Wei", "Fatima", "Diego", "Elena", "Noah", "Maya",
    "Liam", "Sofia", "Omar", "Aisha", "Lucas", "Zoe", "Kenji", "Ines",
]
LAST_NAMES = [
    "Nair", "Lee", "Garcia", "Kim", "Patel", "Chen", "Okafor", "Rossi",
    "Muller", "Silva", "Andersson", "Novak", "Haddad", "Kaur", "Tanaka", "Ivanov",
]

TIERS_WEIGHTED = [("Bronze", 0.40), ("Silver", 0.25), ("Gold", 0.25), ("Platinum", 0.10)]
RETURN_REASONS_WEIGHTED = [
    ("too_small", 0.30), ("too_large", 0.20), ("not_as_described", 0.20),
    ("changed_mind", 0.20), ("defective", 0.10),
]

CATEGORY_CODE = {
    "dress": "DRS", "top": "TOP", "jeans": "JNS", "shoes": "SHO",
    "accessories": "ACC", "jacket": "JKT", "athleisure": "ATH",
}

CATEGORY_NOUNS = {
    "dress": ["Wrap Dress", "Slip Dress", "Shift Dress", "Midi Dress", "Maxi Dress", "A-Line Dress", "Bodycon Dress"],
    "top": ["Cami Top", "Blouse", "Button-Down", "Tank Top", "Crop Top", "Tunic"],
    "jeans": ["Skinny Jeans", "Straight Jeans", "Wide-Leg Jeans", "Bootcut Jeans", "Mom Jeans"],
    "shoes": ["Block Heel Sandal", "Ankle Boot", "Loafer", "Sneaker", "Strappy Heel", "Mule"],
    "accessories": ["Drop Earrings", "Statement Necklace", "Clutch", "Belt", "Scarf"],
    "jacket": ["Blazer", "Bomber Jacket", "Trench Coat", "Denim Jacket", "Cardigan"],
    "athleisure": ["Legging", "Sports Bra", "Joggers", "Zip Hoodie", "Bike Short"],
}

GENERIC_ADJECTIVES = [
    "Silk", "Velvet", "Cotton", "Linen", "Satin", "Chiffon", "Tailored", "Relaxed",
    "Fitted", "Cropped", "Oversized", "Classic", "Modern", "Vintage", "Pleated", "Ribbed",
    "Belted", "Draped", "Structured", "Boxy", "Slim-Fit", "High-Waist", "Wrap", "Button-Front",
]
DENIM_ADJECTIVES = ["Dark Wash", "Light Wash", "Distressed", "High-Rise", "Tailored", "Relaxed", "Classic", "Raw Denim", "Mid-Rise", "Stretch"]
SHOE_ADJECTIVES = ["Leather", "Suede", "Patent", "Woven", "Classic", "Metallic", "Canvas", "Platform", "Pointed-Toe", "Round-Toe"]
ACCESSORY_ADJECTIVES = ["Gold-Tone", "Silver-Tone", "Leather", "Beaded", "Statement", "Classic", "Woven", "Crystal", "Minimalist", "Chunky"]
ATHLEISURE_ADJECTIVES = ["Performance", "Seamless", "High-Waist", "Ribbed", "Classic", "Cropped", "Relaxed", "Moisture-Wick", "Compression", "Lightweight"]

CATEGORY_ADJECTIVES = {
    "dress": GENERIC_ADJECTIVES, "top": GENERIC_ADJECTIVES, "jacket": GENERIC_ADJECTIVES,
    "jeans": DENIM_ADJECTIVES, "shoes": SHOE_ADJECTIVES,
    "accessories": ACCESSORY_ADJECTIVES, "athleisure": ATHLEISURE_ADJECTIVES,
}

# Dummy product images — a retail-grid look without real photography. One flat
# color per category via placehold.co (no key, no account); tier just changes
# the label so premium/private_label are visually distinguishable in the UI.
# Swap _image_url() for a real DAM/CDN lookup later; nothing downstream cares
# how the URL was produced.
CATEGORY_IMAGE_COLOR = {
    "dress": "E8B4BC", "top": "B4CDE6", "jeans": "6C7A96", "shoes": "C9A66B",
    "accessories": "D4AF37", "jacket": "8B7355", "athleisure": "7FA88E",
}


def _image_url(category: str, tier: str) -> str:
    color = CATEGORY_IMAGE_COLOR.get(category, "CCCCCC")
    label = category.replace("_", " ").title() + (" •" if tier == "premium" else "")
    return f"https://placehold.co/300x400/{color}/FFFFFF?text={label.replace(' ', '+')}"

NOUN_OCCASIONS = {
    # Deliberately balanced so "date-night" candidates span multiple categories
    # instead of being dominated by whichever category happens to have the most
    # nouns — dress had 5/7 nouns tagged date-night against jeans' 1/5 in an
    # earlier version of this table, which let dresses swamp Priya's jeans-fit
    # showcase in the demo script purely on candidate-pool size, not relevance.
    "Wrap Dress": "date-night,everyday", "Slip Dress": "date-night,evening", "Shift Dress": "work,everyday",
    "Midi Dress": "date-night,everyday", "Maxi Dress": "date-night,evening", "A-Line Dress": "everyday,work",
    "Bodycon Dress": "date-night,evening", "Cami Top": "date-night,everyday", "Blouse": "work,date-night",
    "Button-Down": "work,everyday", "Tank Top": "everyday,gym", "Crop Top": "date-night,everyday",
    "Tunic": "everyday", "Skinny Jeans": "date-night,everyday", "Straight Jeans": "date-night,work",
    "Wide-Leg Jeans": "work,everyday", "Bootcut Jeans": "date-night,everyday", "Mom Jeans": "everyday",
    "Block Heel Sandal": "date-night,evening", "Ankle Boot": "everyday,work", "Loafer": "work,everyday",
    "Sneaker": "everyday,gym", "Strappy Heel": "date-night,evening", "Mule": "date-night,work",
    "Drop Earrings": "date-night,evening", "Statement Necklace": "date-night,evening", "Clutch": "date-night,evening",
    "Belt": "everyday,work", "Scarf": "everyday,work", "Blazer": "work,date-night", "Bomber Jacket": "everyday",
    "Trench Coat": "work,everyday", "Denim Jacket": "date-night,everyday", "Cardigan": "everyday,work",
    "Legging": "gym,everyday", "Sports Bra": "gym", "Joggers": "gym,everyday",
    "Zip Hoodie": "gym,everyday", "Bike Short": "gym",
}

PRICE_RANGES = {
    ("dress", "premium"): (180, 280), ("dress", "private_label"): (35, 70),
    ("top", "premium"): (70, 120), ("top", "private_label"): (18, 40),
    ("jeans", "premium"): (110, 180), ("jeans", "private_label"): (28, 55),
    ("shoes", "premium"): (140, 220), ("shoes", "private_label"): (35, 70),
    ("accessories", "premium"): (40, 90), ("accessories", "private_label"): (10, 25),
    ("jacket", "premium"): (160, 260), ("jacket", "private_label"): (40, 75),
    ("athleisure", "premium"): (60, 110), ("athleisure", "private_label"): (15, 35),
}


def _weighted_choice(rng: random.Random, weighted: list[tuple]):
    r = rng.random()
    cumulative = 0.0
    for value, weight in weighted:
        cumulative += weight
        if r <= cumulative:
            return value
    return weighted[-1][0]


def _generate_customers(rng: random.Random) -> tuple[list[tuple], list[list]]:
    customers, loyalty = [], []
    for i in range(1, TARGET_CUSTOMERS + 1):
        customer_id = f"CUST-{i:04d}"
        name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        tenure = rng.randint(1, 60)
        tier = _weighted_choice(rng, TIERS_WEIGHTED)
        customers.append((customer_id, name, f"{customer_id.lower()}@example.com", tenure))
        loyalty.append([customer_id, tier, 0, 0.0])  # points/spend filled in after transactions
    return customers, loyalty


def _generate_catalogue(rng: random.Random) -> list[tuple]:
    rows = []
    for category, nouns in CATEGORY_NOUNS.items():
        code = CATEGORY_CODE[category]
        adjectives = CATEGORY_ADJECTIVES[category]
        for tier, prefix in (("premium", "P"), ("private_label", "V")):
            low, high = PRICE_RANGES[(category, tier)]
            n = 0
            for noun in nouns:
                for adjective in adjectives:
                    n += 1
                    sku = f"{prefix}-{code}-{100 + n:04d}"
                    name = f"{adjective} {noun}"
                    price = round(rng.uniform(low, high), 2)
                    trending = 1 if rng.random() < 0.06 else 0
                    tags = NOUN_OCCASIONS.get(noun, "everyday")
                    rows.append((sku, name, category, price, tier, trending, tags))
    return rows


def _generate_transactions_and_returns(rng: random.Random, customers_with_tier, catalogue_rows, now):
    """Returns (transactions, returns, fit_profile_rows, totals_by_customer).

    Orders are generated in chronological order per customer (oldest
    first), and a category's return probability drops from
    TARGET_RETURN_RATE to GUIDED_RETURN_RISK the moment a too_small/
    too_large return establishes guidance for it — checked *before* that
    order's own outcome is decided, so guidance can only affect orders
    that actually come after it. Without this, admin.py's guided-vs-
    baseline comparison has nothing real to measure: the population's
    aggregate return rate would sit at ~34% regardless of fit_profile,
    because nothing in the generator ever encoded guidance *preventing*
    a return — it would just be labeling already-random outcomes after
    the fact.
    """
    category_by_sku = {row[0]: row[2] for row in catalogue_rows}
    price_by_sku = {row[0]: row[3] for row in catalogue_rows}
    all_skus = list(category_by_sku)

    transactions, returns, fit_profile_rows = [], [], []
    totals: dict[str, dict] = {}
    order_seq = return_seq = 0

    for customer_id, tier in customers_with_tier:
        spend, points = 0.0, 0
        # category -> (runs, preferred_size, established_at) — latest signal wins,
        # same rule TAILOR itself uses, just applied forward in time here instead
        # of retroactively across the whole history.
        guidance: dict[str, tuple[str, str, datetime]] = {}

        n_orders = rng.randint(2, 16)
        days_ago_list = sorted((rng.randint(0, MONTHS_OF_HISTORY * 30) for _ in range(n_orders)), reverse=True)

        for days_ago in days_ago_list:
            sku = rng.choice(all_skus)
            category = category_by_sku[sku]
            price = price_by_sku[sku]
            created = now - timedelta(days=days_ago)

            order_seq += 1
            order_id = f"ORD-{order_seq:06d}"
            transactions.append((order_id, customer_id, sku, "order", price, created.strftime("%Y-%m-%d %H:%M:%S")))
            spend += price
            points += round(price * TIER_MULTIPLIER.get(tier, 1.0))

            return_probability = GUIDED_RETURN_RISK if category in guidance else TARGET_RETURN_RATE
            if rng.random() < return_probability:
                return_seq += 1
                return_id = f"RET-BULK-{return_seq:06d}"
                reason = _weighted_choice(rng, RETURN_REASONS_WEIGHTED)
                size_returned = rng.choice(SHOE_SIZES if category == "shoes" else APPAREL_SIZES)
                returned_at = created + timedelta(days=rng.randint(2, 14))
                returns.append((return_id, customer_id, sku, size_returned, reason, returned_at.strftime("%Y-%m-%d %H:%M:%S")))
                if reason in ("too_small", "too_large"):
                    guidance[category] = ("small" if reason == "too_small" else "large", size_returned, returned_at)

        for category, (runs, size, established_at) in guidance.items():
            fit_profile_rows.append((customer_id, category, size, runs, established_at.strftime("%Y-%m-%d %H:%M:%S")))
        totals[customer_id] = {"spend": round(spend, 2), "points": points}

    return transactions, returns, fit_profile_rows, totals


def seed() -> None:
    init_schema()
    rng = random.Random(RNG_SEED)
    now = datetime.now()

    bulk_customers, bulk_loyalty = _generate_customers(rng)
    bulk_catalogue = _generate_catalogue(rng)
    customers_with_tier = [(c[0], l[1]) for c, l in zip(bulk_customers, bulk_loyalty)]
    bulk_transactions, bulk_returns, bulk_fit_profile, totals = _generate_transactions_and_returns(
        rng, customers_with_tier, bulk_catalogue, now
    )
    for row in bulk_loyalty:
        t = totals.get(row[0], {"spend": 0.0, "points": 0})
        row[2], row[3] = t["points"], t["spend"]

    conn = get_connection()
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO customers (customer_id, name, email, tenure_months) VALUES (?, ?, ?, ?)",
            CUSTOMERS + bulk_customers,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO loyalty (customer_id, tier, points_balance, ytd_spend) VALUES (?, ?, ?, ?)",
            LOYALTY + [tuple(row) for row in bulk_loyalty],
        )

        full_catalogue = CATALOGUE + bulk_catalogue
        catalogue_with_images = [row + (_image_url(row[2], row[4]),) for row in full_catalogue]
        conn.executemany(
            """INSERT OR IGNORE INTO catalogue
               (sku, name, category, price, tier, trending, occasion_tags, image_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            catalogue_with_images,
        )

        inventory_rows = []
        for sku, _name, category, *_rest in CATALOGUE:
            for size in (SHOE_SIZES if category == "shoes" else APPAREL_SIZES):
                inventory_rows.append((sku, size, "DC1", 12))  # hero SKUs always stocked
        for sku, _name, category, *_rest in bulk_catalogue:
            for size in (SHOE_SIZES if category == "shoes" else APPAREL_SIZES):
                inventory_rows.append((sku, size, "DC1", rng.randint(0, 20)))
        conn.executemany(
            "INSERT OR IGNORE INTO inventory (sku, size, location, stock_qty) VALUES (?, ?, ?, ?)",
            inventory_rows,
        )

        conn.executemany(
            "INSERT OR IGNORE INTO fit_profile (customer_id, category, preferred_size, runs) VALUES (?, ?, ?, ?)",
            FIT_PROFILE,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO fit_profile (customer_id, category, preferred_size, runs, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            bulk_fit_profile,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO returns (return_id, customer_id, sku, size_returned, reason_code)
               VALUES (?, ?, ?, ?, ?)""",
            RETURNS,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO returns (return_id, customer_id, sku, size_returned, reason_code, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            bulk_returns,
        )
        conn.executemany(
            "INSERT INTO behavioural (customer_id, event_type, sku) VALUES (?, ?, ?)",
            BEHAVIOURAL,
        )
        conn.executemany(
            """INSERT OR IGNORE INTO transactional (order_id, customer_id, sku, kind, amount, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            bulk_transactions,
        )
    conn.close()

    total_customers = len(CUSTOMERS) + len(bulk_customers)
    total_skus = len(full_catalogue)
    total_orders = len(bulk_transactions)
    total_returns = len(RETURNS) + len(bulk_returns)
    achieved_rate = total_returns / total_orders if total_orders else 0.0

    print(f"Seeded {total_customers} customers, {total_skus} SKUs, {len(inventory_rows)} inventory rows, "
          f"{total_orders} orders over {MONTHS_OF_HISTORY} months.")
    print(f"Returns: {total_returns} (target rate {TARGET_RETURN_RATE:.0%}, achieved {achieved_rate:.1%})")

    indexed = vector_store.build_index()
    print(f"Vector index built ({vector_store.backend()} backend): {indexed} SKUs embedded.")


if __name__ == "__main__":
    seed()
