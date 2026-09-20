"""Agent Runtime — MCP-style tool dispatch (Fig. 01/03's «invoke_tool» chokepoint).

Every tool call in the system goes through `invoke_tool`, never a direct
function call between agents. That's what makes the policy check and the
audit log unconditional rather than a convention agents have to remember.
"""

import json

from neutail import contracts, policy
from neutail.db import get_connection


def invoke_tool(caller: str, tool: str, **kwargs) -> dict:
    try:
        policy.check_tool_access(caller, tool)
    except policy.PolicyDenied as denied:
        _log(caller, tool, allowed=False, detail=str(denied))
        raise

    contract = contracts.get(tool)
    result = contract.handler(**kwargs)
    _log(caller, tool, allowed=True, detail=json.dumps({"args": kwargs, "result": result}, default=str))
    return result


def log_event(caller: str, tool: str, allowed: bool, detail: str) -> None:
    """Public logging hook for events that aren't a tool call through invoke_tool
    (e.g. a human reviewer resolving an escalation) but still belong in the
    audit trail per §05: "every call, of either kind, is logged"."""
    _log(caller, tool, allowed, detail)


def _log(caller: str, tool: str, allowed: bool, detail: str) -> None:
    conn = get_connection()
    with conn:
        conn.execute(
            "INSERT INTO audit_log (caller, tool, allowed, detail) VALUES (?, ?, ?, ?)",
            (caller, tool, int(allowed), detail),
        )
    conn.close()


def recent_audit_log(limit: int = 20) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM audit_log ORDER BY log_id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
