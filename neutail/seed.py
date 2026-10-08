"""Seeds the data universe with a rich, repeatable, entirely synthetic estate.

  python -m neutail.seed          # drops and rebuilds data/neutail.db, then the vector index

- Six hand-authored demo personas (the storefront's "Shopping as" picker),
  each set up to show a different part of the system: Priya (affluent Gold,
  jeans run small), Jordan (brand-new, first-time payer), Amara (Platinum,
  Elite offer goes to human review), Sam (Silver, one purchase from Gold),
  Elena (Gold, no marketing consent, shoes run large) and Kenji (lapsing).
- 34 generated customers with 12 months of orders, sizes, returns, browsing.
- ~1,000 SKUs across seven categories, each with a real product photo from
  assets/products/ (matched to the product type), colour, material,
  description, markdowns, new-in and trending flags.

Returns are simulated causally: every customer has a true size per category;
ordering the wrong size is likely to come back too_small / too_large, and once
a fit-related return establishes guidance, later orders in that category use
the right size. The 34% baseline and the guided rate in /admin/outcomes come
out of that process rather than being asserted. Fixed RNG seed → same estate
every run (timestamps are relative to now).
"""

import random
import zlib
from datetime import datetime, timedelta, timezone

from neutail import data, db
from neutail.agents.tally import TIER_MULTIPLIER, tier_for_spend
from neutail.platform import vector_store

RNG_SEED = 20261002
FMT = "%Y-%m-%d %H:%M:%S"
APPAREL_SIZES = ["XS", "S", "M", "L", "XL"]
SHOE_SIZES = ["6", "7", "8", "9", "10"]
CITIES = ["London", "Manchester", "Birmingham", "Leeds", "Glasgow", "Bristol", "Edinburgh", "Liverpool",
          "Cardiff", "Brighton", "Newcastle", "Nottingham"]

PLANS = [
    ("plus", "Neu.Tail+", 60.0, "Personal styling advisory|Fit guarantee: free returns on any miss|Early access to drops"),
    ("elite", "Neu.Tail Elite", 90.0, "Human + agent stylist|Wardrobe management across seasons|White-glove try-at-home|VIP events"),
]

# --------------------------------------------------------------------------- images
IMAGE_COLOUR = {
    "acc-crescent-necklace": "Gold", "acc-gold-bracelet": "Gold", "acc-heart-earrings": "Cobalt",
    "acc-leather-belt": "Tan", "acc-pearl-necklace": "Ivory", "acc-pink-clutch": "Blush",
    "acc-red-handbag": "Scarlet", "acc-sapphire-earrings": "Silver", "acc-scarf-coat": "Chocolate",
    "acc-woven-handbag": "Tan", "ath-grey-hoodie": "Grey Marl", "ath-grey-joggers": "Grey Marl",
    "ath-orange-legging": "Burnt Orange", "ath-orange-sweatshirt": "Orange", "ath-red-sports-bra": "Red",
    "ath-street-joggers": "Khaki", "ath-training-set": "Black", "ath-white-sweatshirt": "White",
    "dress-blue-maxi": "Powder Blue", "dress-burgundy-shirt": "Burgundy", "dress-floral-wrap": "Ivory Floral",
    "dress-plum-offshoulder": "Plum", "dress-red-floral-midi": "Red Floral", "dress-red-flowing-maxi": "Red",
    "dress-red-gown": "Crimson", "dress-white-offshoulder": "White", "jacket-black-leather": "Black",
    "jacket-black-puffer": "Black", "jacket-grey-blazer": "Grey Check", "jacket-knit-cardigan": "Cream",
    "jacket-light-denim": "Light Wash", "jacket-navy-blazer": "Navy", "jacket-navy-suit": "Navy",
    "jacket-olive-utility": "Olive", "jacket-pink-coat": "Blush", "jacket-plaid-blazer": "Navy Check",
    "jacket-rust-bomber": "Rust", "jeans-classic-blue": "Mid Blue", "jeans-distressed-patch": "Dark Blue",
    "jeans-light-distressed": "Light Blue", "jeans-light-wash": "Light Blue", "shoes-black-ankle-boot": "Black",
    "shoes-black-stiletto": "Black", "shoes-brown-ankle-boot": "Dark Brown", "shoes-burgundy-sandal": "Burgundy",
    "shoes-colour-sneaker": "Multicolour", "shoes-floral-stiletto": "Blue Floral", "shoes-lace-heel": "Ivory Lace",
    "shoes-monk-loafer": "Brown", "shoes-street-boot": "Tan", "shoes-tan-brogue": "Tan",
    "shoes-tan-lace-boot": "Tan", "shoes-white-sneaker": "White", "shoes-worn-work-boot": "Olive",
    "top-black-graphic-tee": "Black", "top-black-tee-hanger": "Black", "top-cream-blouse": "Cream",
    "top-floral-blouse": "White Floral", "top-folded-tees": "Assorted", "top-printed-tee": "White",
    "top-white-flatlay-tee": "White", "top-white-tank": "White", "top-white-tee": "White",
}
CATEGORY_PREFIX = {"dress": "dress-", "top": "top-", "jeans": "jeans-", "shoes": "shoes-",
                   "accessories": "acc-", "jacket": "jacket-", "athleisure": "ath-"}
# First matching keyword wins; falls back to every image in the category.
IMAGE_RULES = {
    "dress": [("maxi", ["dress-blue-maxi", "dress-red-flowing-maxi", "dress-red-gown"]),
              ("wrap", ["dress-floral-wrap", "dress-red-floral-midi"]),
              ("slip", ["dress-plum-offshoulder", "dress-white-offshoulder", "dress-red-gown"]),
              ("bodycon", ["dress-red-gown", "dress-plum-offshoulder"]),
              ("shift", ["dress-burgundy-shirt", "dress-white-offshoulder"]),
              ("a-line", ["dress-red-floral-midi", "dress-burgundy-shirt"]),
              ("midi", ["dress-red-floral-midi", "dress-burgundy-shirt", "dress-floral-wrap"])],
    "top": [("blouse", ["top-floral-blouse", "top-cream-blouse"]), ("button", ["top-floral-blouse", "top-cream-blouse"]),
            ("tank", ["top-white-tank", "top-white-tee"]), ("cami", ["top-white-tank", "top-cream-blouse"]),
            ("crop", ["top-black-graphic-tee", "top-floral-blouse"]), ("tunic", ["top-cream-blouse", "top-printed-tee"]),
            ("tee", ["top-white-tee", "top-black-tee-hanger", "top-white-flatlay-tee", "top-printed-tee",
                     "top-black-graphic-tee", "top-folded-tees"])],
    "jeans": [("light", ["jeans-light-wash", "jeans-light-distressed"]), ("distressed", ["jeans-light-distressed", "jeans-distressed-patch"]),
              ("dark", ["jeans-distressed-patch", "jeans-classic-blue"]), ("raw", ["jeans-distressed-patch", "jeans-classic-blue"])],
    "shoes": [("boot", ["shoes-black-ankle-boot", "shoes-brown-ankle-boot", "shoes-tan-lace-boot", "shoes-street-boot",
                        "shoes-worn-work-boot"]),
              ("loafer", ["shoes-monk-loafer", "shoes-tan-brogue"]), ("sneaker", ["shoes-white-sneaker", "shoes-colour-sneaker"]),
              ("sandal", ["shoes-burgundy-sandal"]),
              ("heel", ["shoes-lace-heel", "shoes-black-stiletto", "shoes-floral-stiletto"]),
              ("mule", ["shoes-burgundy-sandal", "shoes-lace-heel"])],
    "accessories": [("earring", ["acc-heart-earrings", "acc-sapphire-earrings"]),
                    ("necklace", ["acc-pearl-necklace", "acc-crescent-necklace"]),
                    ("clutch", ["acc-pink-clutch", "acc-red-handbag"]), ("tote", ["acc-woven-handbag", "acc-red-handbag"]),
                    ("belt", ["acc-leather-belt"]), ("scarf", ["acc-scarf-coat"]), ("bracelet", ["acc-gold-bracelet"])],
    "jacket": [("blazer", ["jacket-plaid-blazer", "jacket-grey-blazer", "jacket-navy-blazer", "jacket-navy-suit"]),
               ("bomber", ["jacket-rust-bomber", "jacket-olive-utility"]), ("trench", ["jacket-pink-coat", "jacket-olive-utility"]),
               ("puffer", ["jacket-black-puffer"]), ("denim", ["jacket-light-denim"]),
               ("cardigan", ["jacket-knit-cardigan"]), ("leather", ["jacket-black-leather"])],
    "athleisure": [("legging", ["ath-orange-legging", "ath-red-sports-bra"]), ("bra", ["ath-red-sports-bra", "ath-training-set"]),
                   ("jogger", ["ath-grey-joggers", "ath-street-joggers"]),
                   ("hoodie", ["ath-grey-hoodie", "ath-orange-sweatshirt", "ath-white-sweatshirt"]),
                   ("short", ["ath-training-set", "ath-red-sports-bra"])],
}


def image_for(sku: str, name: str, category: str) -> str:
    lowered = name.lower()
    pool = next((imgs for kw, imgs in IMAGE_RULES.get(category, []) if kw in lowered), None)
    if pool is None:
        pool = sorted(k for k in IMAGE_COLOUR if k.startswith(CATEGORY_PREFIX[category]))
    return pool[zlib.crc32(sku.encode()) % len(pool)]


def image_url(image: str) -> str:
    return f"/assets/products/{image}.jpg"


# --------------------------------------------------------------------------- catalogue
CATEGORY_NOUNS = {
    "dress": ["Wrap Dress", "Slip Dress", "Shift Dress", "Midi Dress", "Maxi Dress", "A-Line Dress", "Bodycon Dress"],
    "top": ["Cami Top", "Blouse", "Button-Down Shirt", "Tank Top", "Crop Top", "Tunic", "Everyday Tee"],
    "jeans": ["Skinny Jeans", "Straight Jeans", "Wide-Leg Jeans", "Bootcut Jeans", "Mom Jeans"],
    "shoes": ["Block Heel Sandal", "Ankle Boot", "Loafer", "Sneaker", "Strappy Heel", "Mule", "Chelsea Boot"],
    "accessories": ["Drop Earrings", "Statement Necklace", "Clutch", "Tote", "Belt", "Scarf", "Bracelet"],
    "jacket": ["Blazer", "Bomber Jacket", "Trench Coat", "Denim Jacket", "Cardigan", "Leather Jacket", "Puffer Jacket"],
    "athleisure": ["Legging", "Sports Bra", "Joggers", "Zip Hoodie", "Bike Short"],
}
CATEGORY_ADJECTIVES = {
    "dress": ["Silk", "Velvet", "Linen", "Satin", "Chiffon", "Pleated", "Ruched", "Belted", "Draped", "Tiered", "Ribbed", "Jersey"],
    "top": ["Silk", "Linen", "Cotton", "Satin", "Organic Cotton", "Ribbed", "Relaxed", "Fitted", "Cropped", "Oversized", "Sheer", "Poplin"],
    "jeans": ["Dark Wash", "Light Wash", "Distressed", "High-Rise", "Tailored", "Relaxed", "Raw Denim", "Mid-Rise", "Stretch", "Vintage", "Rigid", "Clean"],
    "shoes": ["Leather", "Suede", "Patent", "Woven", "Metallic", "Canvas", "Platform", "Pointed-Toe", "Square-Toe", "Padded", "Croc-Effect", "Classic"],
    "accessories": ["Gold-Tone", "Silver-Tone", "Leather", "Beaded", "Statement", "Woven", "Crystal", "Minimalist", "Chunky", "Pearl", "Enamel", "Vintage"],
    "jacket": ["Tailored", "Wool-Blend", "Cropped", "Oversized", "Quilted", "Double-Breasted", "Relaxed", "Belted", "Structured", "Boxy", "Lightweight", "Classic"],
    "athleisure": ["Performance", "Seamless", "High-Waist", "Ribbed", "Sculpt", "Cropped", "Relaxed", "Moisture-Wick", "Compression", "Lightweight", "Brushed", "Recycled"],
}
NOUN_OCCASIONS = {
    "Wrap Dress": "date-night,everyday", "Slip Dress": "date-night,evening", "Shift Dress": "work,everyday",
    "Midi Dress": "date-night,weekend", "Maxi Dress": "holiday,evening,wedding-guest", "A-Line Dress": "everyday,work",
    "Bodycon Dress": "date-night,party", "Cami Top": "date-night,everyday", "Blouse": "work,date-night",
    "Button-Down Shirt": "work,everyday", "Tank Top": "everyday,gym,holiday", "Crop Top": "date-night,party",
    "Tunic": "everyday,holiday", "Everyday Tee": "everyday,weekend", "Skinny Jeans": "date-night,everyday",
    "Straight Jeans": "work,weekend", "Wide-Leg Jeans": "work,everyday", "Bootcut Jeans": "date-night,everyday",
    "Mom Jeans": "weekend,everyday", "Block Heel Sandal": "date-night,wedding-guest", "Ankle Boot": "everyday,work",
    "Loafer": "work,everyday", "Sneaker": "weekend,everyday,gym", "Strappy Heel": "date-night,party,evening",
    "Mule": "work,date-night", "Chelsea Boot": "weekend,everyday", "Drop Earrings": "date-night,evening,gift",
    "Statement Necklace": "party,evening,gift", "Clutch": "evening,wedding-guest,gift", "Tote": "work,everyday",
    "Belt": "everyday,work", "Scarf": "everyday,gift", "Bracelet": "gift,everyday", "Blazer": "work,date-night",
    "Bomber Jacket": "weekend,everyday", "Trench Coat": "work,everyday", "Denim Jacket": "weekend,date-night",
    "Cardigan": "everyday,work", "Leather Jacket": "date-night,weekend", "Puffer Jacket": "weekend,everyday",
    "Legging": "gym,everyday", "Sports Bra": "gym", "Joggers": "gym,weekend", "Zip Hoodie": "gym,weekend",
    "Bike Short": "gym,holiday",
}
PRICE_RANGES = {
    ("dress", "premium"): (165, 290), ("dress", "private_label"): (32, 72),
    ("top", "premium"): (65, 130), ("top", "private_label"): (14, 38),
    ("jeans", "premium"): (110, 185), ("jeans", "private_label"): (28, 55),
    ("shoes", "premium"): (140, 260), ("shoes", "private_label"): (32, 72),
    ("accessories", "premium"): (45, 180), ("accessories", "private_label"): (9, 28),
    ("jacket", "premium"): (160, 340), ("jacket", "private_label"): (40, 85),
    ("athleisure", "premium"): (55, 110), ("athleisure", "private_label"): (14, 35),
}
CATEGORY_CODE = {"dress": "DRS", "top": "TOP", "jeans": "JNS", "shoes": "SHO", "accessories": "ACC",
                 "jacket": "JKT", "athleisure": "ATH"}
MATERIALS = {
    "dress": "viscose", "top": "cotton", "jeans": "stretch denim", "shoes": "leather", "accessories": "brass",
    "jacket": "wool blend", "athleisure": "recycled nylon",
}
FIT_LINES = {
    "dress": "Cut to skim the body with a fluid drape.", "top": "An easy shape that tucks in or wears loose.",
    "jeans": "Sits at the natural waist with a comfortable stretch.", "shoes": "Cushioned insole for all-day wear.",
    "accessories": "Light enough to wear every day.", "jacket": "Structured shoulders, room to layer underneath.",
    "athleisure": "Four-way stretch that moves with you.",
}

# Hand-authored hero SKUs — referenced by the demo script and personas.
HERO_CATALOGUE = [
    # sku, name, category, price, tier, trending, occasion_tags, image
    ("P-DRS-001", "Silk Wrap Dress", "dress", 228.0, "premium", 0, "date-night,evening", "dress-floral-wrap"),
    ("P-TOP-002", "Satin Cami Top", "top", 96.0, "premium", 0, "date-night", "top-cream-blouse"),
    ("P-JNS-003", "Tailored Dark Denim", "jeans", 148.0, "premium", 0, "date-night,everyday", "jeans-classic-blue"),
    ("P-SHO-004", "Block Heel Sandal", "shoes", 189.0, "premium", 0, "date-night,evening", "shoes-burgundy-sandal"),
    ("P-ACC-005", "Statement Drop Earrings", "accessories", 68.0, "premium", 0, "date-night,evening,gift", "acc-sapphire-earrings"),
    ("P-DRS-006", "Velvet Slip Dress", "dress", 255.0, "premium", 1, "date-night,evening", "dress-plum-offshoulder"),
    ("P-JKT-007", "Cropped Check Blazer", "jacket", 214.0, "premium", 1, "date-night,work", "jacket-plaid-blazer"),
    ("P-ATH-008", "Sculpt Performance Legging", "athleisure", 84.0, "premium", 0, "everyday,gym", "ath-orange-legging"),
    ("V-DRS-101", "Floral Wrap Midi Dress", "dress", 58.0, "private_label", 0, "date-night,everyday", "dress-red-floral-midi"),
    ("V-TOP-102", "Satin-Feel Cami", "top", 32.0, "private_label", 0, "date-night", "top-white-tank"),
    ("V-JNS-103", "Skinny Dark Wash Jeans", "jeans", 44.0, "private_label", 0, "date-night,everyday", "jeans-distressed-patch"),
    ("V-SHO-104", "Strappy Stiletto Heel", "shoes", 52.0, "private_label", 1, "date-night,evening", "shoes-black-stiletto"),
    ("V-ACC-105", "Heart Drop Earrings", "accessories", 18.0, "private_label", 0, "date-night,gift", "acc-heart-earrings"),
    ("V-DRS-106", "Off-Shoulder Slip Dress", "dress", 49.0, "private_label", 1, "date-night,evening", "dress-white-offshoulder"),
    ("V-JKT-107", "Cropped Denim Jacket", "jacket", 56.0, "private_label", 0, "date-night,weekend", "jacket-light-denim"),
    ("V-ATH-108", "Everyday Sports Bra", "athleisure", 22.0, "private_label", 0, "everyday,gym", "ath-red-sports-bra"),
]


def _description(name: str, category: str, colour: str, material: str, occasions: str) -> str:
    occ = [o.replace("-", " ") for o in occasions.split(",") if o]
    return (f"{name} in {colour.lower()}, made from {material}. {FIT_LINES[category]} "
            f"Made for {', '.join(occ[:-1]) + ' and ' + occ[-1] if len(occ) > 1 else occ[0]}.")


def _material(adjective: str, category: str) -> str:
    known = {"Silk": "mulberry silk", "Velvet": "velvet", "Linen": "linen", "Satin": "satin", "Chiffon": "chiffon",
             "Cotton": "cotton", "Organic Cotton": "organic cotton", "Leather": "leather", "Suede": "suede",
             "Wool-Blend": "wool blend", "Canvas": "canvas", "Jersey": "jersey", "Poplin": "cotton poplin",
             "Pearl": "freshwater pearl", "Recycled": "recycled polyester"}
    return known.get(adjective, MATERIALS[category])


def _catalogue(rng: random.Random, now: datetime) -> list[tuple]:
    rows = []

    def add(sku, name, category, price, tier, trending, tags, image):
        colour = IMAGE_COLOUR[image]
        adjective = name.split()[0]
        material = _material(adjective, category)
        was = None
        if rng.random() < 0.11:
            was = round(price / (1 - rng.choice([0.15, 0.2, 0.25, 0.3, 0.4])), 0)
        is_new = 1 if rng.random() < 0.08 else 0
        updated = (now - timedelta(days=rng.randint(0, 6) if is_new else rng.randint(20, 300))).strftime(FMT)
        rows.append((sku, name, category, price, was, tier, trending, tags, colour, material,
                     _description(name, category, colour, material, tags), image_url(image), is_new, updated))

    for sku, name, category, price, tier, trending, tags, image in HERO_CATALOGUE:
        add(sku, name, category, price, tier, trending, tags, image)
    for category, nouns in CATEGORY_NOUNS.items():
        for tier, prefix in (("premium", "P"), ("private_label", "V")):
            low, high = PRICE_RANGES[(category, tier)]
            n = 0
            for noun in nouns:
                for adjective in CATEGORY_ADJECTIVES[category]:
                    n += 1
                    sku = f"{prefix}-{CATEGORY_CODE[category]}-{100 + n:04d}"
                    name = f"{adjective} {noun}"
                    price = round(rng.uniform(low, high)) - 0.01
                    trending = 1 if rng.random() < 0.06 else 0
                    add(sku, name, category, round(price, 2), tier, trending, NOUN_OCCASIONS[noun],
                        image_for(sku, name, category))
    return rows


def sizes_for(category: str) -> list[str]:
    return ["ONE"] if category == "accessories" else SHOE_SIZES if category == "shoes" else APPAREL_SIZES


# --------------------------------------------------------------------------- personas
HEROES = [
    # id, name, email, city, tenure, consent, tier, ytd_spend, points, blurb
    ("CUST-PRIYA", "Priya Nair", "priya.nair@example.com", "London", 18, 1, "Gold", 3100.0, 4200,
     "Affluent · Gold · evening wear lover — jeans run small on her"),
    ("CUST-JORDAN", "Jordan Lee", "jordan.lee@example.com", "Manchester", 2, 1, "Bronze", 0.0, 120,
     "New customer · value · browsing dresses, first purchase pending"),
    ("CUST-AMARA", "Amara Okafor", "amara.okafor@example.com", "London", 52, 1, "Platinum", 6200.0, 9800,
     "Platinum · tailoring & workwear · Elite offer needs human review"),
    ("CUST-SAM", "Sam Patel", "sam.patel@example.com", "Bristol", 26, 1, "Silver", 1350.0, 2100,
     "Silver · athleisure fan · £150 away from Gold"),
    ("CUST-ELENA", "Elena Rossi", "elena.rossi@example.com", "Edinburgh", 30, 0, "Gold", 2400.0, 3600,
     "Gold · no marketing consent · shoes run large on her"),
    ("CUST-KENJI", "Kenji Tanaka", "kenji.tanaka@example.com", "Leeds", 40, 1, "Silver", 700.0, 1500,
     "Silver · lapsing — hasn't shopped in over three months"),
]

# sku, size, days_ago, return_reason (or None)
HERO_ORDERS = {
    "CUST-PRIYA": [("P-DRS-006", "M", 300, None), ("P-JNS-003", "M", 200, "too_small"), ("P-ACC-005", "ONE", 150, None),
                   ("P-SHO-004", "7", 120, None), ("@dress:premium:0", "M", 90, None), ("@top:premium:3", "M", 60, None),
                   ("@jeans:premium:5", "L", 40, None), ("@dress:premium:9", "M", 21, None), ("@accessories:premium:2", "ONE", 9, None)],
    "CUST-AMARA": [("P-JKT-007", "S", 280, None), ("@jacket:premium:0", "S", 200, None), ("@shoes:premium:14", "6", 160, "too_small"),
                   ("@shoes:premium:15", "7", 150, None), ("@jacket:premium:11", "S", 75, None), ("@top:premium:13", "S", 50, None),
                   ("@accessories:premium:40", "ONE", 30, None), ("@jacket:premium:22", "S", 12, None), ("@dress:premium:20", "S", 5, None)],
    "CUST-SAM": [("@athleisure:private_label:0", "L", 250, None), ("@athleisure:private_label:14", "L", 180, None),
                 ("@shoes:private_label:36", "9", 140, None), ("@athleisure:private_label:30", "XL", 100, "too_large"),
                 ("@athleisure:private_label:26", "L", 60, None), ("@top:private_label:80", "L", 25, None)],
    "CUST-ELENA": [("@shoes:premium:2", "8", 260, "too_large"), ("@shoes:premium:30", "8", 190, "too_large"),
                   ("@shoes:premium:44", "7", 120, None), ("@dress:premium:30", "S", 80, None), ("@top:premium:40", "S", 40, None)],
    "CUST-KENJI": [("@jacket:private_label:3", "L", 330, None), ("@jeans:private_label:6", "L", 240, None),
                   ("@top:private_label:20", "L", 160, "changed_mind"), ("@shoes:private_label:50", "9", 110, None)],
}
HERO_BROWSING = {
    "CUST-PRIYA": [("browse", "P-DRS-006", 1), ("browse", "P-DRS-001", 1), ("search", "@dress:premium:4", 2),
                   ("browse", "@jeans:premium:5", 3), ("cart", "P-SHO-004", 5), ("browse", "@accessories:premium:7", 6)],
    "CUST-JORDAN": [("browse", "V-DRS-101", 1), ("browse", "V-DRS-106", 1), ("search", "@dress:private_label:5", 2),
                    ("browse", "@shoes:private_label:3", 4)],
    "CUST-AMARA": [("browse", "@jacket:premium:30", 1), ("browse", "@jacket:premium:2", 2), ("search", "@shoes:premium:20", 3)],
    "CUST-SAM": [("browse", "@athleisure:private_label:40", 0), ("browse", "@shoes:private_label:40", 1),
                 ("search", "@athleisure:private_label:22", 2)],
    "CUST-ELENA": [("browse", "@dress:premium:44", 2), ("browse", "@accessories:premium:12", 4)],
    "CUST-KENJI": [("browse", "@jacket:private_label:15", 110)],
}
HERO_FIT_PROFILE = [("CUST-PRIYA", "jeans", "M", "small", 195), ("CUST-ELENA", "shoes", "8", "large", 185),
                    ("CUST-SAM", "athleisure", "XL", "large", 95), ("CUST-AMARA", "shoes", "6", "small", 145)]

FIRST_NAMES = ["Alex", "Sam", "Taylor", "Morgan", "Casey", "Riley", "Jamie", "Avery", "Wei", "Fatima", "Diego",
               "Noah", "Maya", "Liam", "Sofia", "Omar", "Aisha", "Lucas", "Zoe", "Ines", "Hannah", "Ravi",
               "Chloe", "Tomasz", "Grace", "Yusuf", "Freya", "Arjun", "Isla", "Mateo", "Leila", "Oscar"]
LAST_NAMES = ["Garcia", "Kim", "Patel", "Chen", "Okafor", "Rossi", "Muller", "Silva", "Andersson", "Novak",
              "Haddad", "Kaur", "Ivanov", "Murphy", "Evans", "Begum", "Wright", "Nowak", "Ahmed", "Clarke"]
REASONS_NON_FIT = [("changed_mind", 0.5), ("not_as_described", 0.3), ("defective", 0.2)]
TARGET_CUSTOMERS = 34


def _weighted(rng: random.Random, options: list[tuple]):
    r, acc = rng.random(), 0.0
    for value, weight in options:
        acc += weight
        if r <= acc:
            return value
    return options[-1][0]


def seed() -> None:
    rng = random.Random(RNG_SEED)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    db.init_schema(reset=True)

    catalogue_rows = _catalogue(rng, now)
    by_key: dict[tuple[str, str], list[tuple]] = {}
    for row in catalogue_rows:
        if row[0][2:5] in CATEGORY_CODE.values() and row[0][-4:].isdigit():
            by_key.setdefault((row[2], row[5]), []).append(row)
    price = {r[0]: r[3] for r in catalogue_rows}
    category_of = {r[0]: r[2] for r in catalogue_rows}

    def resolve(ref: str) -> str:
        if not ref.startswith("@"):
            return ref
        category, tier, idx = ref[1:].split(":")
        pool = by_key[(category, tier)]
        return pool[int(idx) % len(pool)][0]

    customers, loyalty_rows, ledger, orders, returns_rows, fit_rows, behavioural_rows = [], [], [], [], [], [], []
    order_seq = 0

    def add_order(customer_id, sku, size, when, tier, reason=None):
        nonlocal order_seq
        order_seq += 1
        order_id = f"ORD-{order_seq:06d}"
        amount = price[sku]
        orders.append((order_id, customer_id, sku, size, 1, "order", None, amount, None, when.strftime(FMT)))
        ledger.append((customer_id, round(amount * TIER_MULTIPLIER[tier]), "order", order_id,
                       f"{TIER_MULTIPLIER[tier]}× on £{amount:.2f}", when.strftime(FMT)))
        if reason:
            returned = when + timedelta(days=rng.randint(3, 12))
            returns_rows.append((f"RET-{len(returns_rows) + 1:06d}", order_id, customer_id, sku, size, reason,
                                 rng.choice("AAB"), returned.strftime(FMT)))
        return order_id

    # ---- heroes
    for cid, name, email, city, tenure, consent, tier, ytd, points, blurb in HEROES:
        customers.append((cid, name, email, city, tenure, consent, blurb,
                          (now - timedelta(days=tenure * 30)).strftime(FMT)))
        earned = 0
        for ref, size, days_ago, reason in HERO_ORDERS.get(cid, []):
            sku = resolve(ref)
            add_order(cid, sku, size, now - timedelta(days=days_ago, hours=rng.randint(1, 20)), tier, reason)
            earned += ledger[-1][1]
        opening = max(points - earned, 0)
        if opening:
            ledger.append((cid, opening, "seed", f"opening:{cid}", "Opening balance (migrated)",
                           (now - timedelta(days=365)).strftime(FMT)))
        loyalty_rows.append((cid, tier, earned + opening, ytd))
        for event, ref, days_ago in HERO_BROWSING.get(cid, []):
            behavioural_rows.append((cid, event, resolve(ref), None,
                                     (now - timedelta(days=days_ago, hours=rng.randint(1, 10))).strftime(FMT)))
    for cid, category, size, runs, days_ago in HERO_FIT_PROFILE:
        fit_rows.append((cid, category, size, runs, (now - timedelta(days=days_ago)).strftime(FMT)))

    # ---- generated population (causal fit/returns simulation)
    all_skus = [r[0] for r in catalogue_rows]
    premium_skus = [s for s in all_skus if s.startswith("P-")]
    value_skus = [s for s in all_skus if s.startswith("V-")]
    for i in range(1, TARGET_CUSTOMERS + 1):
        cid = f"CUST-{i:04d}"
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        tenure = rng.randint(1, 72)
        affluence = rng.random()
        customers.append((cid, f"{first} {last}", f"{first.lower()}.{last.lower()}.{i}@example.com",
                          rng.choice(CITIES), tenure, 1 if rng.random() < 0.85 else 0, "",
                          (now - timedelta(days=tenure * 30)).strftime(FMT)))
        true_size = {c: rng.choice(sizes_for(c)[1:-1]) if c != "accessories" else "ONE" for c in CATEGORY_CODE}
        favourites = rng.sample(list(CATEGORY_CODE), 3)
        guidance: dict[str, str] = {}
        spend, n_orders = 0.0, rng.randint(2, 7) + int(affluence * 12)
        days = sorted((rng.randint(0, 360) for _ in range(n_orders)), reverse=True)
        provisional_tier = "Bronze"
        for days_ago in days:
            pool = premium_skus if rng.random() < affluence else value_skus
            sku = rng.choice(pool)
            if rng.random() < 0.6:  # favour favourite categories
                fav = [s for s in pool if category_of[s] in favourites]
                sku = rng.choice(fav)
            category = category_of[sku]
            scale = sizes_for(category)
            truth = true_size[category]
            wrong_p = 0.05 if category in guidance else 0.45
            size = truth
            if category != "accessories" and rng.random() < wrong_p:
                j = scale.index(truth) + rng.choice([-1, 1])
                size = scale[max(0, min(len(scale) - 1, j))]
            when = now - timedelta(days=days_ago, hours=rng.randint(0, 23))
            reason = None
            if size != truth:
                if rng.random() < 0.8:
                    reason = "too_small" if scale.index(size) < scale.index(truth) else "too_large"
            elif rng.random() < 0.14:
                reason = _weighted(rng, REASONS_NON_FIT)
            add_order(cid, sku, size, when, provisional_tier, reason)
            spend += price[sku]
            provisional_tier = tier_for_spend(spend)
            if reason in ("too_small", "too_large") and category not in guidance:
                guidance[category] = size
                fit_rows.append((cid, category, size, "small" if reason == "too_small" else "large",
                                 (when + timedelta(days=10)).strftime(FMT)))
        tier = tier_for_spend(spend)
        points = sum(entry[1] for entry in ledger if entry[0] == cid)
        loyalty_rows.append((cid, tier, points, round(spend, 2)))
        for _ in range(rng.randint(3, 18)):
            sku = rng.choice([s for s in all_skus if category_of[s] in favourites])
            behavioural_rows.append((cid, rng.choice(["browse", "browse", "search", "cart"]), sku, None,
                                     (now - timedelta(days=rng.randint(0, 30), hours=rng.randint(0, 23))).strftime(FMT)))

    # ---- a few redemptions so the ledger shows both directions
    for cid in ("CUST-0003", "CUST-0011", "CUST-0019"):
        bal = next(l for l in loyalty_rows if l[0] == cid)[2]
        if bal >= 500:
            ledger.append((cid, -500, "redeem", f"seed-redeem:{cid}", "£5 voucher", (now - timedelta(days=20)).strftime(FMT)))
            loyalty_rows = [(l[0], l[1], l[2] - 500, l[3]) if l[0] == cid else l for l in loyalty_rows]

    # ---- escalations so the review queue has real work
    escalation_rows = [
        ("CUST-JORDAN", "order", price["P-DRS-001"], "P-DRS-001", "M", 1, None, 0.6,
         "first-time payer; account under 3 months old; premium dress on a new account", "pending", None, 2),
        ("CUST-0007", "order", round(price[by_key[("jacket", "premium")][4][0]] * 2, 2), by_key[("jacket", "premium")][4][0],
         "M", 2, None, 0.75, "over the £250.00 auto-approve bound for an order; 3.4× this customer's average order",
         "pending", None, 1),
        ("CUST-0012", "subscription", 90.0, None, None, 1, "elite", 0.5,
         "£90.00 is over the £75.00 auto-approve bound for a subscription", "approved", "human_reviewer", 9),
        ("CUST-0021", "order", price[by_key[("shoes", "premium")][7][0]], by_key[("shoes", "premium")][7][0], "8", 1,
         None, 0.55, "4 charges in the last 24h; first-time payer", "denied", "human_reviewer", 15),
    ]

    with data.system_scope(), db.transaction() as conn:
        conn.executemany("INSERT INTO plans (plan, display_name, monthly_price, perks) VALUES (?, ?, ?, ?)", PLANS)
        conn.executemany(
            """INSERT INTO customers (customer_id, name, email, city, tenure_months, marketing_consent, persona_blurb, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", customers)
        conn.executemany("INSERT INTO loyalty (customer_id, tier, points_balance, ytd_spend) VALUES (?, ?, ?, ?)",
                         loyalty_rows)
        conn.executemany(
            """INSERT INTO catalogue (sku, name, category, price, was_price, tier, trending, occasion_tags, colour,
                                      material, description, image_url, is_new, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", catalogue_rows)
        inventory_rows = []
        hero_skus = {h[0] for h in HERO_CATALOGUE}
        for row in catalogue_rows:
            for size in sizes_for(row[2]):
                qty = 15 if row[0] in hero_skus else rng.choice([0, 2, 4, 6, 8, 10, 12, 18, 25])
                inventory_rows.append((row[0], size, "DC1", qty))
                if rng.random() < 0.3:
                    inventory_rows.append((row[0], size, rng.choice(["STORE-LDN", "STORE-MAN", "STORE-EDI"]),
                                           rng.randint(1, 6)))
        conn.executemany("INSERT OR IGNORE INTO inventory (sku, size, location, stock_qty) VALUES (?, ?, ?, ?)",
                         inventory_rows)
        conn.executemany(
            """INSERT INTO transactional (order_id, customer_id, sku, size, quantity, kind, plan, amount,
                                          idempotency_key, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", orders)
        conn.executemany(
            """INSERT INTO returns (return_id, order_id, customer_id, sku, size_returned, reason_code, condition_grade,
                                    created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", returns_rows)
        conn.executemany(
            "INSERT OR REPLACE INTO fit_profile (customer_id, category, preferred_size, runs, created_at) VALUES (?, ?, ?, ?, ?)",
            fit_rows)
        conn.executemany(
            "INSERT INTO behavioural (customer_id, event_type, sku, query, created_at) VALUES (?, ?, ?, ?, ?)",
            behavioural_rows)
        conn.executemany(
            "INSERT INTO loyalty_ledger (customer_id, delta, source, ref_id, note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ledger)
        for cid, kind, amount, sku, size, qty, plan, risk, reason, status, by, days_ago in escalation_rows:
            created = (now - timedelta(days=days_ago)).strftime(FMT)
            resolved = (now - timedelta(days=days_ago - 1)).strftime(FMT) if status != "pending" else None
            conn.execute(
                """INSERT INTO escalations (customer_id, kind, amount, sku, size, quantity, plan, risk_score, reason,
                                            status, created_at, resolved_at, resolved_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (cid, kind, amount, sku, size, qty, plan, risk, reason, status, created, resolved, by))

    n_returns = len(returns_rows)
    print(f"Seeded {len(customers)} customers ({len(HEROES)} demo personas), {len(catalogue_rows)} SKUs with photos, "
          f"{len(orders)} orders, {n_returns} returns ({n_returns / len(orders):.1%}), "
          f"{len(behavioural_rows)} behavioural events, {len(escalation_rows)} escalations.")
    indexed = vector_store.build_index()
    print(f"Vector index built ({vector_store.backend()} backend): {indexed} SKUs.")


if __name__ == "__main__":
    seed()
