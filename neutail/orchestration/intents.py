"""Intent sensing — classify a message into one or more intents *with
entities* (category, ordinal), using the fast-tier model when available and a
keyword/entity matcher as the deterministic fallback.
"""

import json
import re
from dataclasses import dataclass

from neutail.platform import audit, gateway, prompts

CONFIDENCE_THRESHOLD = 0.5
VALID_INTENTS = {"discovery", "fit", "service", "loyalty", "accept_offer", "unknown"}
CATEGORIES = ("dress", "top", "jeans", "shoes", "accessories", "jacket", "athleisure")

CATEGORY_SYNONYMS = {
    "dress": ["dress", "dresses", "gown", "maxi", "midi", "slip dress"],
    "top": ["top", "tops", "blouse", "blouses", "tee", "t-shirt", "tshirt", "shirt", "cami", "tank", "tunic"],
    "jeans": ["jeans", "denim jeans", "jean", "denims"],
    "shoes": ["shoes", "shoe", "heels", "heel", "boots", "boot", "sneakers", "trainers", "loafers", "sandals",
              "mules", "pumps", "stilettos"],
    "accessories": ["accessories", "accessory", "earrings", "necklace", "jewellery", "jewelry", "bag", "bags",
                    "clutch", "handbag", "belt", "scarf", "bracelet"],
    "jacket": ["jacket", "jackets", "blazer", "blazers", "coat", "coats", "trench", "bomber", "cardigan",
               "outerwear"],
    "athleisure": ["athleisure", "leggings", "legging", "gym", "workout", "yoga", "joggers", "hoodie",
                   "sports bra", "activewear", "running"],
}

_KEYWORDS = {
    "fit": ["size", "sizing", "fit", "fits", "runs small", "runs large", "true to size", "too small",
            "too big"],
    "service": ["stylist", "styling", "style advice", "help me style", "subscription", "personal shopper"],
    "loyalty": ["points", "loyalty", "my tier", "rewards", "reward", "redeem", "voucher", "badges",
                "challenge", "next tier"],
    "accept_offer": ["sign me up", "subscribe", "i'll take it", "yes please", "accept the offer",
                     "go ahead", "yes, sign"],
    "discovery": ["show me", "find me", "looking for", "something for", "outfit", "look for", "ideas",
                  "recommend", "new arrivals", "new in", "what's new", "gift", "date night", "weekend",
                  "wedding", "holiday", "work", "office", "party", "trending", "sale", "deals"],
}
_KEYWORD_RES = {name: [re.compile(r"(?<![\w-])" + re.escape(kw) + r"(?![\w-])") for kw in kws]
                for name, kws in _KEYWORDS.items()}
_ORDINAL_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7,
                  "eighth": 8, "last": -1}
_ORDINAL_WORD_RE = re.compile(r"\b(" + "|".join(_ORDINAL_WORDS) + r")\b(?!-)")
_ORDINAL_NUM_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b|#(\d+)\b")
_SKU_RE = re.compile(r"\b([PV]-[A-Z]{3}-\d{3,4})\b")


@dataclass
class Intent:
    intent: str
    confidence: float
    category: str | None = None
    ordinal: int | None = None

    def as_dict(self) -> dict:
        return {"intent": self.intent, "confidence": self.confidence, "category": self.category,
                "ordinal": self.ordinal}


_CATEGORY_RES = [(category, re.compile(r"(?<![\w-])" + re.escape(word) + r"(?![\w-])"))
                 for category, words in CATEGORY_SYNONYMS.items() for word in words]


def extract_category(text: str) -> str | None:
    """Earliest product-type word in the text, mapped to its category."""
    q = text.lower()
    best = None
    for category, pattern in _CATEGORY_RES:
        m = pattern.search(q)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), category)
    return best[1] if best else None


def extract_ordinal(text: str) -> int | None:
    q = text.lower()
    m = _ORDINAL_WORD_RE.search(q)
    if m:
        return _ORDINAL_WORDS[m.group(1)]
    m = _ORDINAL_NUM_RE.search(q)
    if m:
        return int(m.group(1) or m.group(2))
    return None


def extract_sku(text: str) -> str | None:
    m = _SKU_RE.search(text.upper())
    return m.group(1) if m else None


def _split_clauses(message: str) -> list[str]:
    parts = re.split(r",?\s+\band\b\s+|;\s*|\balso\b", message, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def classify_keywords(message: str, has_recent_results: bool = False, has_pending_offer: bool = False) -> list[Intent]:
    found: list[Intent] = []
    for clause in _split_clauses(message) or [message]:
        q = clause.lower()
        category = extract_category(clause)
        ordinal = extract_ordinal(clause)
        hits = [name for name, patterns in _KEYWORD_RES.items() if any(p.search(q) for p in patterns)]
        if "accept_offer" in hits and not has_pending_offer:
            hits.remove("accept_offer")
            hits.append("service")
        if "fit" in hits:
            intent = Intent("fit", 0.85, category, ordinal)
        elif "accept_offer" in hits:
            intent = Intent("accept_offer", 0.85)
        elif "loyalty" in hits:
            intent = Intent("loyalty", 0.85)
        elif "service" in hits:
            intent = Intent("service", 0.85)
        elif "discovery" in hits or category:
            intent = Intent("discovery", 0.85 if "discovery" in hits else 0.75, category)
        elif has_recent_results and ordinal is not None:
            intent = Intent("fit", 0.7, None, ordinal)
        else:
            intent = Intent("unknown", 0.2)
        if all(i.intent != intent.intent for i in found):
            found.append(intent)
    real = [i for i in found if i.intent != "unknown"]
    return real or [Intent("unknown", 0.2)]


def classify(message: str, has_recent_results: bool, has_pending_offer: bool) -> tuple[list[Intent], bool]:
    """Returns (intents, used_live_model)."""
    fallback = classify_keywords(message, has_recent_results, has_pending_offer)
    prompt_obj = prompts.get("orchestrator_intent")
    payload = json.dumps({"message": message, "has_recent_results": has_recent_results,
                          "has_pending_offer": has_pending_offer})
    text, live = gateway.call_model("lead_orchestrator", "fast", prompt_obj, payload,
                                    fallback=lambda: json.dumps([i.as_dict() for i in fallback]))
    if not live:
        return fallback, False
    try:
        parsed = json.loads(gateway.extract_json(text))
        if not isinstance(parsed, list) or not parsed:
            raise ValueError("empty classification")
        intents = []
        for item in parsed[:3]:
            intent, confidence = item["intent"], float(item["confidence"])
            category = item.get("category")
            if intent not in VALID_INTENTS or not 0 <= confidence <= 1 or (category and category not in CATEGORIES):
                raise ValueError(f"bad entry {item}")
            ordinal = item.get("ordinal")
            intents.append(Intent(intent, confidence, category or extract_category(message) if intent in
                                  ("discovery", "fit") else None, int(ordinal) if ordinal else None))
        return intents, True
    except Exception as exc:
        audit.record(kind="model", caller="lead_orchestrator", tool="model_gateway:fast", outcome="fallback",
                     detail={"reason": f"unusable classification: {type(exc).__name__}: {exc}"})
        return fallback, False
