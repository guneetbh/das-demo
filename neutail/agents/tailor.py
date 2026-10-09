"""TAILOR — UC3 size & fit. Also the evaluator in MUSE's evaluator-optimizer
loop (MUSE calls get_fit_profile per candidate category).

Return risk is computed per customer and category, not asserted:
  baseline  = the category's population return rate
  personal  = this customer's returns / orders in the category, shrunk toward
              the baseline (K pseudo-orders) so one return doesn't swing it
  guided    = personal x the estate's measured guidance effect, once GRADE's
              fit-related reason codes tell us which way the category runs
Size direction comes from GRADE reason codes (too_small / too_large) plus any
recorded fit profile; the GRADE -> TAILOR loop is what the deck's outcome-based
pricing depends on.
"""

import json
from collections import Counter

from pydantic import BaseModel, Field

from neutail.context import Context
from neutail.data import behavioural, catalogue, fit, returns, transactional
from neutail.platform import audit, contracts, gateway, prompts

AGENT_NAME = "tailor_agent"
DEFAULT_BASELINE = 0.34
SHRINKAGE_K = 4
APPAREL_SIZES = ["XS", "S", "M", "L", "XL"]
SHOE_SIZES = ["6", "7", "8", "9", "10"]


class FitIn(BaseModel):
    category: str | None = Field(default=None, pattern="^(dress|top|jeans|shoes|accessories|jacket|athleisure)$")
    sku: str | None = Field(default=None, max_length=32)
    customer_note: str | None = Field(default=None, max_length=500)
    log_event: bool = False


def _baseline(category: str) -> float:
    orders = transactional.category_order_count(category)
    if orders < 10:
        return DEFAULT_BASELINE
    return round(returns.category_return_count(category) / orders, 4)


def _step(size: str | None, direction: int, category: str) -> str | None:
    scale = SHOE_SIZES if category == "shoes" else APPAREL_SIZES
    if size not in scale:
        return size
    return scale[max(0, min(len(scale) - 1, scale.index(size) + direction))]


def _fallback_note(structured: dict, note: str) -> str:
    return json.dumps({"live_guidance": f"Noted — we'll keep \"{note}\" in mind. {structured['guidance'].capitalize()}."})


def _blend_note(category: str, structured: dict, note: str) -> tuple[str, bool]:
    prompt = prompts.get("tailor_note")
    payload = json.dumps({
        "category": category, "has_history": structured["has_history"],
        "standard_guidance": structured["guidance"], "recommended_size": structured.get("recommended_size"),
        "return_risk": structured["return_risk"], "customer_note": note,
    })
    text, live = gateway.call_model(AGENT_NAME, "fast", prompt, payload, fallback=lambda: _fallback_note(structured, note))
    try:
        guidance = json.loads(gateway.extract_json(text))["live_guidance"]
        clean, violations = prompts.filter_output(guidance, max_chars=220)
        if violations:
            audit.record(kind="model", caller=AGENT_NAME, tool="output_filter", outcome="filtered",
                         detail={"violations": violations})
        return clean, live
    except Exception as exc:
        if live:
            audit.record(kind="model", caller=AGENT_NAME, tool="model_gateway:fast", outcome="fallback",
                         detail={"reason": f"unusable live response: {type(exc).__name__}"})
        return json.loads(_fallback_note(structured, note))["live_guidance"], False


def _get_fit_profile(ctx: Context, category: str | None, sku: str | None, customer_note: str | None,
                     log_event: bool) -> dict:
    item = catalogue.get(sku) if sku else None
    if sku and item is None:
        raise ValueError(f"unknown sku: {sku}")
    category = category or (item or {}).get("category")
    if not category:
        raise ValueError("name a category or a product to size")

    baseline = _baseline(category)
    orders = transactional.orders_in_category(ctx.customer_id, category)
    past_returns = returns.for_customer_category(ctx.customer_id, category)
    profile = fit.get_profile(ctx.customer_id, category)

    reasons = Counter(r["reason_code"] for r in past_returns)
    small, large = reasons.get("too_small", 0), reasons.get("too_large", 0)
    if profile and profile["runs"] in ("small", "large"):
        small += profile["runs"] == "small"
        large += profile["runs"] == "large"
    runs = "small" if small > large else "large" if large > small else ("true" if orders else None)

    returned_ids = {(r["sku"], r["size_returned"]) for r in past_returns}
    kept_sizes = [o["size"] for o in orders if o["size"] and (o["sku"], o["size"]) not in returned_ids]
    direction = +1 if runs == "small" else -1 if runs == "large" else 0
    if kept_sizes:
        # A size they kept already fits — recommend it as-is.
        preferred = Counter(kept_sizes).most_common(1)[0][0]
        recommended = preferred
    else:
        # Only know the size that didn't work (profile or last return): step away from it.
        preferred = (profile or {}).get("preferred_size") or (past_returns[-1]["size_returned"] if past_returns else None)
        recommended = _step(preferred, direction, category)

    personal = (len(past_returns) + SHRINKAGE_K * baseline) / (len(orders) + SHRINKAGE_K)
    has_direction = runs in ("small", "large")
    effect = fit.guidance_effect()
    risk = personal * effect if has_direction else personal
    if item is not None:
        sku_orders = transactional.sku_order_count(item["sku"])
        if sku_orders >= 5:
            sku_rate = (returns.sku_return_count(item["sku"]) + SHRINKAGE_K * baseline) / (sku_orders + SHRINKAGE_K)
            risk = risk * (sku_rate / baseline if baseline else 1.0)
    risk = round(min(0.95, max(0.02, risk)), 3)

    has_history = bool(orders or past_returns or profile)
    if not has_history:
        guidance = "no fit history in this category yet — start with the standard size guide"
    elif runs in ("small", "large") and kept_sizes:
        guidance = (f"go with {recommended}, the size you've kept — your returns show this category "
                    f"runs {runs} on you, so avoid sizing {'down' if runs == 'small' else 'up'}")
    elif runs == "small":
        guidance = f"size up to {recommended} — your past returns show this category runs small on you"
    elif runs == "large":
        guidance = f"size down to {recommended} — your past returns show this category runs large on you"
    elif preferred:
        guidance = f"stay true to size at {preferred} — what you've kept in this category fits"
    else:
        guidance = "true to size — no fit issues in your history for this category"

    result = {
        "category": category,
        "sku": sku,
        "has_history": has_history,
        "preferred_size": preferred,
        "recommended_size": recommended,
        "runs": runs,
        "guidance": guidance,
        "return_risk": risk,
        "baseline_return_risk": baseline,
        "guidance_effect": effect if has_direction else None,
        "evidence": {"orders": len(orders), "returns": len(past_returns), "too_small": reasons.get("too_small", 0),
                     "too_large": reasons.get("too_large", 0), "profile": bool(profile)},
        "used_live_model": False,
    }
    note = prompts.clean_user_text(customer_note)
    if note:
        result["customer_note"] = note
        result["live_guidance"], result["used_live_model"] = _blend_note(category, result, note)
    if log_event:
        behavioural.log(ctx.customer_id, "fit_check", [sku or (item or {}).get("sku")], query=category)
    return result


contracts.register(
    "get_fit_profile",
    owner=AGENT_NAME,
    allowed_callers=["lead_orchestrator", "muse_agent", "storefront", "care_agent", "admin", "external_mcp"],
    handler=_get_fit_profile,
    input_model=FitIn,
    domains={"catalogue": "R", "returns": "R", "transactional": "R", "fit": "R", "behavioural": "W"},
    description="Fit guidance and personal return risk for a category or SKU; optional free-text note "
                "is blended in by a fast-tier model call.",
)
