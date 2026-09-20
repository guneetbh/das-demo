"""MUSE — UC2 discovery (§04: provides rank_products).

Ranks a persona-driven feed with the reasoning model tier, then runs
the evaluator-optimizer loop from §03: every candidate's return-risk
comes from TAILOR's get_fit_profile, called directly (Fig. 03's
dashed feedback edge), before the feed goes back to the Orchestrator.
"""

import json

from neutail import contracts, gateway, runtime
from neutail.agents import tailor  # noqa: F401 — import registers get_fit_profile
from neutail.db import get_connection

AGENT_NAME = "muse_agent"

SYSTEM_PROMPT = (
    "You are MUSE, a product-ranking agent for an apparel retailer. Given a customer "
    "segment, a query, and a list of candidate SKUs (each with tier, price, trending flag "
    "and return_risk from the fit-profile service), pick and rank the best matches. "
    "Prefer 'premium' items for the affluent segment and 'private_label' for the value "
    "segment. Penalize high return_risk. Reply with ONLY a JSON array, best first, of "
    "objects: {\"sku\": str, \"reason\": str (one short sentence)}."
)


def _infer_occasion_tag(query: str) -> str | None:
    q = query.lower()
    if "date" in q and "night" in q:
        return "date-night"
    if "work" in q:
        return "work"
    if "gym" in q or "workout" in q:
        return "gym"
    return None


def _in_stock(conn, sku: str) -> bool:
    row = conn.execute(
        "SELECT COALESCE(SUM(stock_qty), 0) AS total FROM inventory WHERE sku = ?", (sku,)
    ).fetchone()
    return row["total"] > 0


def _candidates(customer_id: str, query: str) -> list[dict]:
    tag = _infer_occasion_tag(query)
    conn = get_connection()
    if tag:
        rows = conn.execute(
            "SELECT * FROM catalogue WHERE occasion_tags LIKE ?", (f"%{tag}%",)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM catalogue").fetchall()

    out = []
    for row in rows:
        if not _in_stock(conn, row["sku"]):
            continue
        # evaluator loop: MUSE calls TAILOR's tool directly (Fig. 03)
        fit = runtime.invoke_tool(
            AGENT_NAME, "get_fit_profile", customer_id=customer_id, category=row["category"]
        )
        out.append(
            {
                "sku": row["sku"],
                "name": row["name"],
                "category": row["category"],
                "price": row["price"],
                "tier": row["tier"],
                "trending": bool(row["trending"]),
                "return_risk": fit["return_risk"],
            }
        )
    conn.close()
    return out


def _fallback_rank(candidates: list[dict], segment: str, top_n: int) -> str:
    preferred_tier = "premium" if segment == "affluent" else "private_label"
    scored = []
    for c in candidates:
        score = (2 if c["tier"] == preferred_tier else 0) + (1 if c["trending"] else 0) - c["return_risk"]
        reason = (
            f"{c['tier']} pick for the {segment} segment"
            + (", trending" if c["trending"] else "")
            + f"; return-risk {c['return_risk']:.0%}"
        )
        scored.append({"sku": c["sku"], "reason": reason, "_score": score})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return json.dumps(scored[:top_n])


def _rank_products(customer_id: str, segment: str, query: str, top_n: int = 6) -> dict:
    candidates = _candidates(customer_id, query)
    by_sku = {c["sku"]: c for c in candidates}

    prompt = json.dumps({"segment": segment, "query": query, "candidates": candidates})
    text, live = gateway.call_model(
        AGENT_NAME, "reasoning", SYSTEM_PROMPT, prompt,
        fallback=lambda: _fallback_rank(candidates, segment, top_n),
    )

    results = []
    try:
        ranked = json.loads(text)
        for item in ranked[:top_n]:
            base = by_sku.get(item.get("sku"))
            if base is None:
                continue
            results.append({**base, "reason": item.get("reason", "")})
        if not results:
            raise ValueError("model returned no usable ranking")
    except Exception:
        ranked = json.loads(_fallback_rank(candidates, segment, top_n))
        results = [{**by_sku[item["sku"]], "reason": item["reason"]} for item in ranked if item["sku"] in by_sku]

    return {"query": query, "segment": segment, "used_live_model": live, "results": results}


contracts.register(
    "rank_products",
    allowed_callers=["muse_agent", "lead_orchestrator"],
    handler=_rank_products,
)


def run(customer_id: str, segment: str, query: str, top_n: int = 6) -> dict:
    """MUSE's entry point — the Orchestrator calls this (Fig. 02, step 3)."""
    return runtime.invoke_tool(
        AGENT_NAME, "rank_products", customer_id=customer_id, segment=segment, query=query, top_n=top_n
    )
