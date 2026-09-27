"""TAILOR — UC3 fit guidance (§04: provides get_fit_profile).

Also the provider side of the evaluator-optimizer loop (§03, Fig. 03):
MUSE calls this same tool to re-rank against return-risk before its
feed ships, so "muse_agent" is on the allow-list from the start. That
loop calls get_fit_profile() up to once per distinct candidate category
(muse.py's _candidates(), deduped — ~7 calls, not ~500) and NEVER passes
customer_note, so it stays exactly as fast as before the note/live-call
path below existed — no live model in the hot path that has to run
before MUSE's own reasoning-tier call even starts.

customer_note is optional free text a customer types about their body or
fit preferences ("I have wide calves", "usually between two sizes") —
structured data the seeded fit_profile/returns tables were never going
to have. When present, one live fast-tier call blends it with the
deterministic guidance below into a short personalized sentence. Fast
tier, not reasoning: this is a short, bounded text-blend of data already
computed, not multi-step judgment — same reasoning classify_intents()
uses fast tier for in orchestrator.py.
"""

import json

from neutail import contracts, gateway, mcp_client, runtime
from neutail.db import get_connection

AGENT_NAME = "tailor_agent"

# Used only if the DB has no order history yet (e.g. before the seed script
# has run) — once seeded, _population_baseline_return_risk() replaces this
# with a number actually computed from the returns/orders the seed
# generator produced, instead of an asserted constant.
_DEFAULT_BASELINE_RETURN_RISK = 0.34
GUIDED_RETURN_RISK = 0.12

TAILOR_NOTE_SYSTEM_PROMPT = (
    "You are TAILOR, a fit-guidance agent for an apparel retailer. You're given a "
    "customer's category-level sizing data (whether they have fit history, the "
    "standard guidance derived from it, and a return-risk figure) plus a short "
    "free-text note they typed about their body or fit preferences. Write ONE "
    "short, specific, second-person sentence of practical sizing advice that "
    "combines both. Do not repeat the note back verbatim. Do not invent data you "
    "weren't given — no specific measurements, no brand claims beyond what's in "
    "standard_guidance. If the note conflicts with the historical pattern, say so "
    "plainly rather than silently picking one. Reply with ONLY JSON: "
    '{"live_guidance": "<one sentence>"}'
)


def _population_baseline_return_risk(conn, category: str) -> float:
    orders = conn.execute(
        """SELECT COUNT(*) AS n FROM transactional
           WHERE kind = 'order' AND sku IN (SELECT sku FROM catalogue WHERE category = ?)""",
        (category,),
    ).fetchone()["n"]
    if orders == 0:
        return _DEFAULT_BASELINE_RETURN_RISK
    returns = conn.execute(
        """SELECT COUNT(*) AS n FROM returns
           WHERE sku IN (SELECT sku FROM catalogue WHERE category = ?)""",
        (category,),
    ).fetchone()["n"]
    return round(returns / orders, 4)


def _fallback_note_guidance(structured: dict, customer_note: str) -> str:
    """Deterministic fallback — no real reasoning over the note (can't, without
    a model), but still acknowledges it rather than silently discarding it, and
    still returns the same JSON shape a live response would, same pattern as
    muse.py's _fallback_rank()."""
    return json.dumps(
        {
            "live_guidance": (
                f'noted: "{customer_note}" — no live model available right now to factor that in; '
                f"standard guidance: {structured['guidance']}"
            )
        }
    )


def _live_note_guidance(category: str, structured: dict, customer_note: str) -> tuple[str, bool]:
    prompt = json.dumps(
        {
            "category": category,
            "has_history": structured["has_history"],
            "standard_guidance": structured["guidance"],
            "return_risk": structured["return_risk"],
            "preferred_size": structured.get("preferred_size"),
            "runs": structured.get("runs"),
            "customer_note": customer_note,
        }
    )
    text, live = gateway.call_model(
        AGENT_NAME,
        "fast",
        TAILOR_NOTE_SYSTEM_PROMPT,
        prompt,
        fallback=lambda: _fallback_note_guidance(structured, customer_note),
    )
    try:
        parsed = json.loads(gateway.extract_json(text))
        return parsed["live_guidance"], live
    except Exception as exc:
        # Same distinction muse.py's _rank_products draws: `live` can be True
        # here (the API call succeeded) even though this branch runs, e.g. a
        # truncated/malformed response — report used_live_model on whether the
        # RESULT came from the model, not on whether the gateway reached it.
        if live:
            runtime.log_event(
                AGENT_NAME, "model_gateway:fast", allowed=True,
                detail=f"live response unusable, fell back: {type(exc).__name__}: {exc}",
            )
        fallback_parsed = json.loads(_fallback_note_guidance(structured, customer_note))
        return fallback_parsed["live_guidance"], False


def _get_fit_profile(customer_id: str, category: str, customer_note: str | None = None) -> dict:
    conn = get_connection()
    profile = conn.execute(
        """SELECT preferred_size, runs FROM fit_profile
           WHERE customer_id = ? AND category = ?""",
        (customer_id, category),
    ).fetchone()

    if profile is None:
        baseline = _population_baseline_return_risk(conn, category)
        conn.close()
        result = {
            "has_history": False,
            "guidance": "no fit history yet — using the standard size guide",
            "return_risk": baseline,
        }
    else:
        returns = conn.execute(
            """SELECT size_returned, reason_code FROM returns
               WHERE customer_id = ? AND sku IN (
                   SELECT sku FROM catalogue WHERE category = ?
               )""",
            (customer_id, category),
        ).fetchall()
        conn.close()

        runs = profile["runs"]
        if runs == "small":
            guidance = f"size up from {profile['preferred_size']} — past returns show this category runs small"
        elif runs == "large":
            guidance = f"size down from {profile['preferred_size']} — past returns show this category runs large"
        else:
            guidance = f"true to size at {profile['preferred_size']}"

        result = {
            "has_history": True,
            "preferred_size": profile["preferred_size"],
            "runs": runs,
            "guidance": guidance,
            "return_risk": GUIDED_RETURN_RISK,
            "prior_returns": [dict(row) for row in returns],
        }

    if customer_note:
        live_guidance, used_live = _live_note_guidance(category, result, customer_note)
        result["customer_note"] = customer_note
        result["live_guidance"] = live_guidance
        result["used_live_model"] = used_live
    else:
        result["used_live_model"] = False

    return result


contracts.register(
    "get_fit_profile",
    allowed_callers=["tailor_agent", "lead_orchestrator", "muse_agent", "mcp_client"],
    handler=_get_fit_profile,
)


async def run(customer_id: str, category: str, customer_note: str | None = None) -> dict:
    """TAILOR's entry point — the Orchestrator calls this (Fig. 02, step 5).
    customer_note is optional; see the module docstring for why it's safe to
    leave off MUSE's evaluator-loop calls."""
    return await mcp_client.call_tool(
        AGENT_NAME, "get_fit_profile", customer_id=customer_id, category=category, customer_note=customer_note
    )
