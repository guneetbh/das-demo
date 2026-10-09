"""Agent Runtime — the one path every tool call takes.

invoke(ctx, tool, **args):
  1. resolves the contract (LookupError -> 404)
  2. policy: caller ACL, then a rate limit at the system boundary (PolicyDenied -> 403)
  3. binds the customer: a shopper-bound context can only act for its own
     customer; staff/agent contexts must name one (PolicyDenied / ValueError)
  4. validates arguments against the contract's input model (ValueError -> 400)
  5. runs the handler *as the contract's owning agent*, inside a data scope
     limited to the contract's declared domains, inside an audit span

Every outcome — ok, denied, invalid, error — is audited with the trace_id.
Identity is never taken from arguments: nested calls a handler makes go out
as the owning agent because the runtime switched `ctx.principal`, not because
the handler chose a name.
"""

from contextvars import ContextVar

from neutail import data
from neutail.context import Context
from neutail.platform import audit, contracts, policy

_depth: ContextVar[int] = ContextVar("neutail_invoke_depth", default=0)

BOUNDARY_ROLES = {"shopper", "reviewer", "admin", "external"}
UNBOUND_ROLES = {"reviewer", "admin", "external", "agent", "system"}


def invoke(ctx: Context, tool: str, **args) -> dict:
    contract = contracts.get(tool)
    caller = ctx.principal
    depth = _depth.get()

    try:
        policy.check_tool_access(caller, contract)
        if depth == 0 and ctx.role in BOUNDARY_ROLES:
            policy.check_rate(ctx.actor, tool, contract.side_effect)
    except policy.PolicyDenied as denied:
        audit.record(kind="tool", caller=caller, tool=tool, outcome="denied", allowed=False,
                     detail={"reason": denied.reason})
        raise

    try:
        handler_ctx = _bind_customer(ctx, contract, args)
        clean_args = contract.validate(args)
    except policy.PolicyDenied as denied:
        audit.record(kind="tool", caller=caller, tool=tool, outcome="denied", allowed=False,
                     detail={"reason": denied.reason})
        raise
    except ValueError as invalid:
        audit.record(kind="tool", caller=caller, tool=tool, outcome="invalid", allowed=True,
                     detail={"reason": str(invalid)})
        raise

    detail = {"args": clean_args, "customer_id": handler_ctx.customer_id}
    token = _depth.set(depth + 1)
    try:
        with audit.span(kind="tool", caller=caller, tool=tool, detail=detail) as span:
            with data.scope(contract.owner, contract.domains):
                result = contract.handler(handler_ctx.as_caller(contract.owner), **clean_args)
            span.detail["result"] = result
            if isinstance(result, dict) and result.get("_outcome"):
                span.outcome = result.pop("_outcome")
        return result
    finally:
        _depth.reset(token)


def _bind_customer(ctx: Context, contract, args: dict) -> Context:
    requested = args.pop("customer_id", None)
    if not contract.customer_scoped:
        return ctx
    if ctx.customer_id:
        if requested and requested != ctx.customer_id:
            raise policy.PolicyDenied(ctx.principal, contract.name,
                                      "context is bound to a different customer than the one requested")
        return ctx
    if ctx.role not in UNBOUND_ROLES:
        raise policy.PolicyDenied(ctx.principal, contract.name, "no customer bound to this session")
    if not requested:
        raise ValueError(f"{contract.name} needs a customer_id for an unbound {ctx.role} principal")
    return ctx.for_customer(requested)
