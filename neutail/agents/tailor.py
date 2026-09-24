"""TAILOR — UC3 fit guidance (§04: provides get_fit_profile).

Also the provider side of the evaluator-optimizer loop (§03, Fig. 03):
MUSE calls this same tool to re-rank against return-risk before its
feed ships, so "muse_agent" is on the allow-list from the start.
"""

from neutail import contracts, runtime
from neutail.db import get_connection

AGENT_NAME = "tailor_agent"

# Used only if the DB has no order history yet (e.g. before the seed script
# has run) — once seeded, _population_baseline_return_risk() replaces this
# with a number actually computed from the returns/orders the seed
# generator produced, instead of an asserted constant.
_DEFAULT_BASELINE_RETURN_RISK = 0.34
GUIDED_RETURN_RISK = 0.12


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


def _get_fit_profile(customer_id: str, category: str) -> dict:
    conn = get_connection()
    profile = conn.execute(
        """SELECT preferred_size, runs FROM fit_profile
           WHERE customer_id = ? AND category = ?""",
        (customer_id, category),
    ).fetchone()

    if profile is None:
        baseline = _population_baseline_return_risk(conn, category)
        conn.close()
        return {
            "has_history": False,
            "guidance": "no fit history yet — using the standard size guide",
            "return_risk": baseline,
        }

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

    return {
        "has_history": True,
        "preferred_size": profile["preferred_size"],
        "runs": runs,
        "guidance": guidance,
        "return_risk": GUIDED_RETURN_RISK,
        "prior_returns": [dict(row) for row in returns],
    }


contracts.register(
    "get_fit_profile",
    allowed_callers=["tailor_agent", "lead_orchestrator", "muse_agent"],
    handler=_get_fit_profile,
)


def run(customer_id: str, category: str) -> dict:
    """TAILOR's entry point — the Orchestrator calls this (Fig. 02, step 5)."""
    return runtime.invoke_tool(AGENT_NAME, "get_fit_profile", customer_id=customer_id, category=category)
