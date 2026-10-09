"""The per-turn context object (deck slide 8: "one context object passed
agent-to-agent").

`principal` is who is calling *right now* and is set by the API layer (from
authentication) or by the runtime (when a tool handler calls another tool, it
calls as the tool's owning agent). Nothing in a request body can set it.
`customer_id` is the customer the request acts for — bound from the shopper's
token, never from the payload. Staff principals (reviewer, admin, external
MCP) have no bound customer and must name one per call.
"""

import uuid
from dataclasses import dataclass, field, replace


@dataclass
class Context:
    principal: str
    customer_id: str | None = None
    session_id: str | None = None
    actor: str = "system"
    role: str = "agent"                     # shopper | reviewer | admin | external | agent | system
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    segment: dict | None = None             # PERSONA's output, published once per turn
    last_results: list[dict] = field(default_factory=list)
    pending_offer: dict | None = None

    def as_caller(self, principal: str) -> "Context":
        return replace(self, principal=principal)

    def for_customer(self, customer_id: str) -> "Context":
        return replace(self, customer_id=customer_id)

    # Only the parts worth keeping between turns go to session memory.
    def memory(self) -> dict:
        return {
            "segment": self.segment,
            "last_results": self.last_results,
            "pending_offer": self.pending_offer,
        }

    def restore(self, memory: dict | None) -> "Context":
        if memory:
            self.segment = memory.get("segment")
            self.last_results = memory.get("last_results") or []
            self.pending_offer = memory.get("pending_offer")
        return self


def system_context(principal: str = "system") -> Context:
    return Context(principal=principal, actor="system", role="system")
