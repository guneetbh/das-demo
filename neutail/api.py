"""Agent Mesh Service (Fig. 04) — FastAPI over the Orchestrator and the
Agent Runtime. This is the :8000 container: Streamlit (or any HTTP
client) talks to this, never to an agent module directly.

Run: uvicorn neutail.api:app --reload --port 8000
"""

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from neutail import admin, contracts, human_review, orchestrator, policy, runtime, session_store
from neutail.agents import tally
from neutail.db import get_connection

app = FastAPI(title="Neu.Tail — Agent Mesh Service", version="0.1.0")


class ChatRequest(BaseModel):
    session_id: str
    customer_id: str
    message: str


class ToolInvokeRequest(BaseModel):
    caller: str
    args: dict[str, Any] = {}


class ResolveEscalationRequest(BaseModel):
    approve: bool
    resolved_by: str = "human_reviewer"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "session_backend": session_store.backend()}


@app.get("/tools")
def list_tools() -> dict:
    """The declared tool contracts (§05) — what Agent Runtime knows how to invoke."""
    return {"tools": contracts.all_tools()}


@app.post("/tools/{tool_name}/invoke")
def invoke_tool(tool_name: str, body: ToolInvokeRequest) -> dict:
    """Generic pass-through to the Agent Runtime — the same chokepoint every
    agent module calls internally, just reachable over HTTP for testing."""
    try:
        return runtime.invoke_tool(body.caller, tool_name, **body.args)
    except policy.PolicyDenied as denied:
        raise HTTPException(status_code=403, detail=str(denied))
    except LookupError as missing:
        raise HTTPException(status_code=404, detail=str(missing))
    except ValueError as bad_input:
        raise HTTPException(status_code=400, detail=str(bad_input))


@app.post("/chat")
def chat(body: ChatRequest) -> dict:
    """The demo client's one endpoint — the Orchestrator does the rest."""
    return orchestrator.handle_message(body.session_id, body.customer_id, body.message)


@app.get("/audit")
def audit(limit: int = 20) -> dict:
    return {"entries": runtime.recent_audit_log(limit)}


@app.get("/catalogue/{sku}")
def catalogue_item(sku: str) -> dict:
    """Public product master data — no agent, no tool contract, no policy
    check. Exists so a product detail page has something to render on a
    fresh load (bookmark, refresh, shared link), not just when reached by
    clicking through a just-run search whose results are still in the
    client's own session state."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM catalogue WHERE sku = ?", (sku,)).fetchone()
    conn.close()
    if row is None:
        raise HTTPException(status_code=404, detail=f"no such SKU: {sku}")
    return dict(row)


@app.get("/customers/{customer_id}/loyalty")
def loyalty_status(customer_id: str) -> dict:
    """Read-only — does not award points. TALLY's earn_points only fires
    from a completed purchase (commit_subscription or an approved escalation)."""
    try:
        return tally.status(customer_id)
    except ValueError as not_found:
        raise HTTPException(status_code=404, detail=str(not_found))


@app.get("/admin/outcomes")
def admin_outcomes() -> dict:
    """Business outcomes the prototype targets (§09) — computed from the
    seeded population, not asserted constants. See neutail/admin.py for
    what each number does and doesn't claim."""
    return admin.business_outcomes()


@app.get("/escalations")
def escalations(status: Optional[str] = "pending") -> dict:
    return {"escalations": human_review.list_escalations(status)}


@app.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(escalation_id: int, body: ResolveEscalationRequest) -> dict:
    try:
        return human_review.resolve_escalation(escalation_id, body.approve, body.resolved_by)
    except ValueError as bad_request:
        raise HTTPException(status_code=400, detail=str(bad_request))
