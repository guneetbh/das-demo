"""Policy engine (§05 — policy-as-code, tool access).

An embedded rule evaluator rather than OPA/Rego, per §08's time-boxed
fallback — same default-deny shape as the Rego example in the doc:
deny unless the caller is on the tool's allow-list.
"""

from neutail import contracts


class PolicyDenied(PermissionError):
    def __init__(self, caller: str, tool: str, reason: str):
        self.caller = caller
        self.tool = tool
        self.reason = reason
        super().__init__(f"{caller} cannot call {tool}: {reason}")


def check_tool_access(caller: str, tool: str) -> None:
    """Raises PolicyDenied if `caller` is not allowed to invoke `tool`."""
    contract = contracts.get(tool)
    if caller not in contract.allowed_callers:
        raise PolicyDenied(
            caller,
            tool,
            f"not in allowed_callers {sorted(contract.allowed_callers)}",
        )
