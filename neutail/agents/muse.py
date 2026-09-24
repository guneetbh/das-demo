"""MUSE — UC2 discovery (§04: provides rank_products).

Ranks a persona-driven feed with the reasoning model tier, then runs
the evaluator-optimizer loop from §03: every candidate's return-risk
comes from TAILOR's get_fit_profile, called directly (Fig. 03's
dashed feedback edge), before the feed goes back to the Orchestrator.

Every ranking it returns is also logged to `behavioural` as a durable
'search' event — not to session memory, which has a TTL and is meant to
disappear, but to the same table PERSONA already reads for
preference_tags. That's what makes "recommendations reflect today's
search" survive a session (or a Redis restart): the next call to
_candidates() sees it via ENGAGEMENT_WINDOW_HOURS, same as any other
returning-customer signal.
"""

import json

from neutail import contracts, gateway, runtime
from neutail.agents import tailor  # noqa: F401 — import registers get_fit_profile
from neutail.db import get_connection

AGENT_NAME = "muse_agent"
ENGAGEMENT_WINDOW_HOURS = 24  # "today", approximated as a rolling window rather than a calendar day
MAX_PER_CATEGORY = 2  # diversity cap on final results — see _apply_diversity_cap
PROMPT_CANDIDATE_LIMIT = 60  # cap on what gets sent to a live model — see _prefilter_for_prompt
PROMPT_MAX_PER_CATEGORY = 12  # roomier than MAX_PER_CATEGORY; this is pre-filtering, not final selection

SYSTEM_PROMPT = (
    "You are MUSE, a product-ranking agent for an apparel retailer. Given a customer "
    "segment, a query, and a list of candidate SKUs (each with tier, price, trending flag, "
    "return_risk from the fit-profile service, and recently_engaged — whether the customer "
    "searched or browsed this category in the last 24h), rank up to return_count of the best "
    "matches, best first — a wide, varied pool, not just the single best category. "
    "Prefer 'premium' items for the affluent segment and 'private_label' for the value "
    "segment. Give recently_engaged items a modest boost, all else equal — this is a "
    "returning customer, not a cold start. Penalize high return_risk. Reply with ONLY a "
    "JSON array, best first, of objects: {\"sku\": str, \"reason\": str (one short sentence)}."
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


def _recently_engaged_categories(conn, customer_id: str) -> set[str]:
    """Categories this customer browsed or searched in the last
    ENGAGEMENT_WINDOW_HOURS, read straight from the durable behavioural
    table — this is "today's search" surviving past the session."""
    rows = conn.execute(
        f"""SELECT DISTINCT c.category
            FROM behavioural b JOIN catalogue c ON c.sku = b.sku
            WHERE b.customer_id = ?
              AND b.created_at >= datetime('now', '-{ENGAGEMENT_WINDOW_HOURS} hours')""",
        (customer_id,),
    ).fetchall()
    return {row["category"] for row in rows}


def _candidates(customer_id: str, query: str) -> list[dict]:
    tag = _infer_occasion_tag(query)
    conn = get_connection()
    if tag:
        rows = conn.execute(
            "SELECT * FROM catalogue WHERE occasion_tags LIKE ?", (f"%{tag}%",)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM catalogue").fetchall()

    engaged_categories = _recently_engaged_categories(conn, customer_id)

    # get_fit_profile depends only on (customer_id, category), never the SKU —
    # cache it per category instead of calling it once per candidate. At 1,300+
    # SKUs but 7 categories, that's the difference between ~500 tool calls
    # (and audit_log rows) per query and ~7.
    fit_by_category: dict[str, dict] = {}

    out = []
    for row in rows:
        if not _in_stock(conn, row["sku"]):
            continue
        category = row["category"]
        if category not in fit_by_category:
            # evaluator loop: MUSE calls TAILOR's tool directly (Fig. 03)
            fit_by_category[category] = runtime.invoke_tool(
                AGENT_NAME, "get_fit_profile", customer_id=customer_id, category=category
            )
        fit = fit_by_category[category]
        out.append(
            {
                "sku": row["sku"],
                "name": row["name"],
                "category": row["category"],
                "price": row["price"],
                "tier": row["tier"],
                "trending": bool(row["trending"]),
                "return_risk": fit["return_risk"],
                "recently_engaged": row["category"] in engaged_categories,
                "image_url": row["image_url"],
            }
        )
    conn.close()
    return out


def _log_search(customer_id: str, results: list[dict]) -> None:
    if not results:
        return
    conn = get_connection()
    with conn:
        conn.executemany(
            "INSERT INTO behavioural (customer_id, event_type, sku) VALUES (?, 'search', ?)",
            [(customer_id, r["sku"]) for r in results],
        )
    conn.close()


def _fallback_rank(candidates: list[dict], segment: str) -> str:
    """Scores and sorts ALL candidates — not just the eventual top_n. The
    diversity cap needs the full ranked list to find each category's best
    items; handing it an already-truncated top-N slice (as an earlier
    version of this function did) defeats it, since a dominant category can
    fill that slice entirely on raw score before any other category gets
    a look-in."""
    preferred_tier = "premium" if segment == "affluent" else "private_label"
    scored = []
    for c in candidates:
        score = (
            (2 if c["tier"] == preferred_tier else 0)
            + (1.5 if c["recently_engaged"] else 0)
            + (1 if c["trending"] else 0)
            - c["return_risk"]
        )
        reason = (
            f"{c['tier']} pick for the {segment} segment"
            + (", trending" if c["trending"] else "")
            + (", seen earlier today" if c["recently_engaged"] else "")
            + f"; return-risk {c['return_risk']:.0%}"
        )
        scored.append({"sku": c["sku"], "reason": reason, "category": c["category"], "_score": score})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return json.dumps(scored)


def _apply_diversity_cap(ranked_pool: list[dict], top_n: int, max_per_category: int = MAX_PER_CATEGORY) -> list[dict]:
    """Fills top_n from a ranked pool, capping how many items from the same
    category can appear. Without this, a category with far more SKUs than
    others (and so more absolute trending items, at a flat per-SKU trending
    rate) can swamp every slot on raw count alone, regardless of how any
    other category's items score — this is what let dresses fill 20/20
    slots at 1,300+ SKUs even when a jeans item was scoring higher than
    most individual dresses (§ discovered via testing at seed-data scale).
    """
    selected, counts, overflow = [], {}, []
    for item in ranked_pool:
        if len(selected) == top_n:
            break
        if counts.get(item["category"], 0) < max_per_category:
            selected.append(item)
            counts[item["category"]] = counts.get(item["category"], 0) + 1
        else:
            overflow.append(item)
    if len(selected) < top_n:
        # not enough distinct categories to fill top_n under the cap — relax it
        # rather than under-fill, still taking overflow in ranked order
        selected.extend(overflow[: top_n - len(selected)])
    return selected


def _prefilter_for_prompt(candidates: list[dict], segment: str, limit: int = PROMPT_CANDIDATE_LIMIT) -> list[dict]:
    """Bounds what actually gets sent to a live model. Discovered via testing:
    sending all 674 date-night candidates produced a 171K-character prompt,
    the model's response hit max_tokens mid-JSON (stop_reason='max_tokens'),
    and the truncated JSON failed to parse — silently falling back to the
    deterministic ranker on every call, real key or not. Diversity-capped
    at a much roomier threshold than the final result (12 vs. 2 per
    category) so the model still has real choices, not just pre-decided
    ones — it's a pre-filter, not a ranking.
    """
    if len(candidates) <= limit:
        return candidates
    scored = json.loads(_fallback_rank(candidates, segment))
    by_sku = {c["sku"]: c for c in candidates}
    pool = [by_sku[item["sku"]] for item in scored if item["sku"] in by_sku]
    return _apply_diversity_cap(pool, top_n=limit, max_per_category=PROMPT_MAX_PER_CATEGORY)


def _rank_products(customer_id: str, segment: str, query: str, top_n: int = 6) -> dict:
    candidates = _candidates(customer_id, query)
    by_sku = {c["sku"]: c for c in candidates}
    # Bounds the live model's *response* (defensive parsing limit) and what the
    # prompt asks it to return — not what the fallback ranker considers, which
    # is always every candidate, so the diversity cap always has a full pool.
    response_bound = min(len(candidates), max(top_n * 4, 16))
    prompt_candidates = _prefilter_for_prompt(candidates, segment)

    prompt = json.dumps({"segment": segment, "query": query, "return_count": response_bound, "candidates": prompt_candidates})
    text, live = gateway.call_model(
        AGENT_NAME, "reasoning", SYSTEM_PROMPT, prompt,
        fallback=lambda: _fallback_rank(candidates, segment),
    )

    ranked_pool = []
    used_live_results = False
    try:
        ranked = json.loads(text)
        if live:
            # response_bound is a defensive limit on an external model's output
            # only — the deterministic fallback already returns exactly the
            # full candidate list, sized for the diversity cap below, and
            # re-truncating it here would silently undo that (as an earlier
            # version of this function did: gateway.call_model's fallback
            # text is valid JSON too, so it always took this try branch, not
            # the except below, and got bounded right back down anyway).
            ranked = ranked[:response_bound]
        for item in ranked:
            base = by_sku.get(item.get("sku"))
            if base is None:
                continue
            ranked_pool.append({**base, "reason": item.get("reason", "")})
        if not ranked_pool:
            raise ValueError("model returned no usable ranking")
        used_live_results = live
    except Exception as exc:
        # `live` can be True here (the API call itself succeeded) even though
        # this branch runs — e.g. a truncated or malformed response. Report
        # used_live_model on whether the RESULTS came from the model, not on
        # whether the gateway reached it; the two silently diverged before
        # this fix, so a truncated-JSON failure looked identical to a real
        # live ranking in the response. Logged, not swallowed — this branch
        # firing with live=True means the model was reached but its response
        # was unusable, worth knowing about even though the fallback keeps
        # the demo working either way.
        if live:
            runtime.log_event(AGENT_NAME, "model_gateway:reasoning", allowed=True,
                               detail=f"live response unusable, fell back: {type(exc).__name__}: {exc}")
        ranked = json.loads(_fallback_rank(candidates, segment))
        ranked_pool = [{**by_sku[item["sku"]], "reason": item["reason"]} for item in ranked if item["sku"] in by_sku]

    results = _apply_diversity_cap(ranked_pool, top_n)
    _log_search(customer_id, results)
    return {"query": query, "segment": segment, "used_live_model": used_live_results, "results": results}


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
