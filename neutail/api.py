"""Agent Mesh Service (Fig. 04) — FastAPI over the Orchestrator and the
Agent Runtime. This is the :8000 container: Streamlit (or any HTTP
client) talks to this, never to an agent module directly.

Run: uvicorn neutail.api:app --reload --port 8000
"""

from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from neutail import contracts, human_review, orchestrator, policy, runtime

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
    return {"status": "ok"}


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


@app.get("/escalations")
def escalations(status: Optional[str] = "pending") -> dict:
    return {"escalations": human_review.list_escalations(status)}


@app.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(escalation_id: int, body: ResolveEscalationRequest) -> dict:
    try:
        return human_review.resolve_escalation(escalation_id, body.approve, body.resolved_by)
    except ValueError as bad_request:
        raise HTTPException(status_code=400, detail=str(bad_request))
