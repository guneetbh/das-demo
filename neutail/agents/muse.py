"""MUSE — UC2 personalised discovery.

Pipeline: semantic candidates (vector store) or, for the home page, the
customer's affinity categories → in-stock filter (one query) → SCOUT trend
and PACE price context → TAILOR return risk per category (evaluator loop) →
rank (reasoning model for typed searches, deterministic ranker otherwise) →
diversity cap → behavioural logging.

Typed searches are logged as 'search' events; recommendation grids as
'impression' events, which never feed back into engagement boosts.
"""

import json

from pydantic import BaseModel, Field

from neutail.context import Context
from neutail.data import behavioural, catalogue, inventory
from neutail.platform import audit, contracts, gateway, prompts, runtime, vector_store

AGENT_NAME = "muse_agent"
ENGAGEMENT_WINDOW_HOURS = 24
MAX_PER_CATEGORY = 2
PROMPT_CANDIDATE_LIMIT = 60
PROMPT_MAX_PER_CATEGORY = 12
SEMANTIC_CANDIDATE_LIMIT = 300


class RankIn(BaseModel):
    query: str = Field(default="", max_length=500)
    top_n: int = Field(default=8, ge=1, le=24)
    source: str = Field(default="search", pattern="^(search|recommendation|similar)$")
    category: str | None = Field(default=None, pattern="^(dress|top|jeans|shoes|accessories|jacket|athleisure)$")
    exclude_sku: str | None = Field(default=None, max_length=32)


class BrowseIn(BaseModel):
    sku: str = Field(min_length=3, max_length=32)


def _segment(ctx: Context) -> dict:
    if ctx.segment:
        return ctx.segment
    return runtime.invoke(ctx, "get_customer_segment")


def _candidate_rows(ctx: Context, query: str, source: str, category: str | None,
                    segment: dict) -> tuple[list[dict], dict, dict]:
    """(rows, semantic score by sku, affinity by sku)."""
    if query and source != "recommendation":
        hits = vector_store.semantic_search(query, top_k=SEMANTIC_CANDIDATE_LIMIT, caller=AGENT_NAME)
        scores = {h["sku"]: h["score"] for h in hits}
        rows = list(catalogue.many(list(scores)).values())
        if category:
            rows = [r for r in rows if r["category"] == category]
        if rows:
            return rows, scores, {}
    # Home-page recommendations (or a query with no semantic hits): draw from
    # the customer's affinity categories plus what's trending, new and on sale.
    preferred = segment.get("preference_tags") or []
    pool: dict[str, dict] = {}
    for cat in ([category] if category else preferred[:3]):
        for row in catalogue.browse(cat, limit=80):
            pool[row["sku"]] = row
    for row in catalogue.browse(category, trending=True, limit=60) + catalogue.browse(category, new_only=True, limit=40) \
            + catalogue.browse(category, on_sale=True, limit=40):
        pool[row["sku"]] = row
    affinity_rank = {c: 1.0 - i * 0.25 for i, c in enumerate(preferred[:4])}
    return list(pool.values()), {}, {sku: affinity_rank.get(row["category"], 0.0) for sku, row in pool.items()}


def _candidates(ctx: Context, query: str, source: str, category: str | None, exclude_sku: str | None,
                segment: dict) -> list[dict]:
    rows, semantic, affinity = _candidate_rows(ctx, query, source, category, segment)
    rows = [r for r in rows if r["sku"] != exclude_sku]
    stocked = inventory.in_stock([r["sku"] for r in rows])
    rows = [r for r in rows if r["sku"] in stocked]
    if not rows:
        return []

    skus = [r["sku"] for r in rows]
    trend = runtime.invoke(ctx, "get_trend_signal", skus=skus[:400])
    prices = runtime.invoke(ctx, "get_price_context", skus=skus[:400])["prices"]
    engaged = behavioural.engaged_categories(ctx.customer_id, ENGAGEMENT_WINDOW_HOURS)
    momentum = trend["category_momentum"]

    fit_by_category: dict[str, dict] = {}
    out = []
    for row in rows:
        cat = row["category"]
        if cat not in fit_by_category:
            fit_by_category[cat] = runtime.invoke(ctx, "get_fit_profile", category=cat)
        price = prices.get(row["sku"], {})
        out.append({
            "sku": row["sku"], "name": row["name"], "category": cat, "colour": row["colour"],
            "price": row["price"], "was_price": row["was_price"], "on_sale": bool(price.get("on_sale")),
            "discount_pct": price.get("discount_pct", 0), "low_stock": bool(price.get("low_stock")),
            "tier": row["tier"], "trending": bool(row["trending"]), "is_new": bool(row["is_new"]),
            "category_momentum": momentum.get(cat, 0.0),
            "return_risk": fit_by_category[cat]["return_risk"],
            "recently_engaged": cat in engaged,
            "semantic_score": semantic.get(row["sku"], 0.0),
            "affinity": affinity.get(row["sku"], 0.0),
            "image_url": row["image_url"],
        })
    return out


def _fallback_rank(candidates: list[dict], segment: dict, source: str = "search") -> str:
    """Scores every candidate (the diversity cap needs the full ordering)."""
    preferred_tier = "premium" if segment["segment"] == "affluent" else "private_label"
    threshold = vector_store.match_threshold()
    scored = []
    for c in candidates:
        score = (
            (2 if c["tier"] == preferred_tier else 0)
            + 2.5 * c["semantic_score"]
            + 1.5 * c["affinity"]
            + (1.5 if c["recently_engaged"] else 0)
            + (1 if c["trending"] else 0)
            + 0.5 * c["category_momentum"]
            + (0.75 if c["on_sale"] and segment["segment"] == "value" else 0)
            + (0.4 if c["is_new"] else 0)
            - 1.5 * c["return_risk"]
        )
        bits = []
        if c["semantic_score"] >= threshold:
            bits.append("a similar style" if source == "similar" else "a close match for what you searched")
        elif c["affinity"] >= 0.75:
            bits.append(f"picked from your favourite category, {c['category']}")
        if c["recently_engaged"]:
            bits.append("in a category you've been browsing")
        if c["trending"]:
            bits.append("trending right now")
        if c["on_sale"]:
            bits.append(f"{c['discount_pct']}% off")
        if c["is_new"]:
            bits.append("just landed")
        if c["return_risk"] < 0.15:
            bits.append("a reliable fit for you")
        lead = "A premium pick" if c["tier"] == "premium" else "Great value"
        reason = lead + (" — " + ", ".join(bits[:3]) if bits else f" in {c['category']}")
        scored.append({"sku": c["sku"], "reason": reason + ".", "_score": score})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return json.dumps(scored)


def apply_diversity_cap(ranked_pool: list[dict], top_n: int, max_per_category: int = MAX_PER_CATEGORY) -> list[dict]:
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
        selected.extend(overflow[: top_n - len(selected)])
    return selected


def _prefilter_for_prompt(candidates: list[dict], segment: dict) -> list[dict]:
    if len(candidates) <= PROMPT_CANDIDATE_LIMIT:
        return candidates
    by_sku = {c["sku"]: c for c in candidates}
    pool = [by_sku[i["sku"]] for i in json.loads(_fallback_rank(candidates, segment))]
    return apply_diversity_cap(pool, PROMPT_CANDIDATE_LIMIT, PROMPT_MAX_PER_CATEGORY)


def _rank_products(ctx: Context, query: str, top_n: int, source: str, category: str | None,
                   exclude_sku: str | None) -> dict:
    query = prompts.clean_user_text(query)
    if source == "similar":
        anchor = catalogue.require(exclude_sku) if exclude_sku else None
        if anchor is None:
            raise ValueError("similar-item recommendations need the anchor sku in exclude_sku")
        query = query or f"{anchor['colour']} {anchor['name']} {anchor['occasion_tags'].replace(',', ' ')}"
    segment = _segment(ctx)
    candidates = _candidates(ctx, query, source, category, exclude_sku, segment)
    by_sku = {c["sku"]: c for c in candidates}
    max_per_category = top_n if category else MAX_PER_CATEGORY

    used_live = False
    ranked_pool: list[dict] = []
    if candidates and source == "search" and query:
        prompt_obj = prompts.get("muse_rank")
        response_bound = min(len(candidates), max(top_n * 3, 16))
        payload = json.dumps({
            "segment": segment["segment"], "micro_segment": segment.get("micro_segment"), "query": query,
            "return_count": response_bound,
            "candidates": [{k: c[k] for k in ("sku", "name", "category", "colour", "price", "tier", "trending",
                                              "on_sale", "return_risk", "recently_engaged", "semantic_score")}
                           for c in _prefilter_for_prompt(candidates, segment)],
        })
        text, live = gateway.call_model(AGENT_NAME, "reasoning", prompt_obj, payload,
                                        fallback=lambda: _fallback_rank(candidates, segment))
        try:
            ranked = json.loads(gateway.extract_json(text))
            if live:
                ranked = ranked[:response_bound]
            for item in ranked:
                base = by_sku.get(item.get("sku"))
                if base is not None:
                    reason, _ = prompts.filter_output(str(item.get("reason", "")), max_chars=160)
                    ranked_pool.append({**base, "reason": reason})
            if not ranked_pool:
                raise ValueError("no usable ranking")
            used_live = live
        except Exception as exc:
            if live:
                audit.record(kind="model", caller=AGENT_NAME, tool="model_gateway:reasoning", outcome="fallback",
                             detail={"reason": f"unusable live response: {type(exc).__name__}: {exc}"})
            ranked_pool = []
    if not ranked_pool and candidates:
        ranked_pool = [{**by_sku[i["sku"]], "reason": i["reason"]}
                       for i in json.loads(_fallback_rank(candidates, segment, source))]

    results = apply_diversity_cap(ranked_pool, top_n, max_per_category)
    event = "search" if source == "search" and query else "impression"
    behavioural.log(ctx.customer_id, event, [r["sku"] for r in results], query=query or None)
    return {"query": query, "source": source, "segment": segment["segment"],
            "micro_segment": segment.get("micro_segment"), "used_live_model": used_live, "results": results}


def _log_browse(ctx: Context, sku: str) -> dict:
    """Record that the customer opened a product page — a real engagement signal."""
    item = catalogue.get(sku)
    if item is None:
        raise ValueError(f"unknown sku: {sku}")
    behavioural.log(ctx.customer_id, "browse", [sku])
    return {"logged": True, "sku": sku, "category": item["category"]}


def _recently_viewed(ctx: Context) -> dict:
    """The customer's recently opened products, newest first."""
    skus = behavioural.recently_viewed(ctx.customer_id)
    items = catalogue.many(skus)
    return {"results": [items[s] for s in skus if s in items]}


contracts.register(
    "rank_products", owner=AGENT_NAME, allowed_callers=["lead_orchestrator", "admin"],
    handler=_rank_products, input_model=RankIn,
    domains={"catalogue": "R", "inventory": "R", "behavioural": "RW"},
    description="Personalised product ranking: semantic retrieval, trend/price context, fit-risk evaluator "
                "loop, model or deterministic ranking, diversity cap.",
)
contracts.register(
    "log_browse", owner=AGENT_NAME, allowed_callers=["storefront"], handler=_log_browse,
    input_model=BrowseIn, domains={"catalogue": "R", "behavioural": "W"}, side_effect=False,
)
contracts.register(
    "recently_viewed", owner=AGENT_NAME, allowed_callers=["storefront", "lead_orchestrator"],
    handler=_recently_viewed, domains={"catalogue": "R", "behavioural": "R"},
)
