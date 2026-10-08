"""Experience API (FastAPI, :8000).

Every route resolves a principal from authentication — a shopper token or a
staff API key — builds a Context with a fresh trace_id, and calls the
orchestrator or runtime.invoke(). No route accepts a caller identity or a
customer_id from the body for shopper actions.

Run: uvicorn neutail.api:app --port 8000      (neutail/api/__init__.py re-exports `app`)
"""

import uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from neutail import agents, config, data, db, reporting  # noqa: F401 — agents registers contracts
from neutail.api.auth import Principal, issue_token, require_role, shopper, staff
from neutail.context import Context
from neutail.orchestration import orchestrator
from neutail.data import catalogue, escalations, inventory
from neutail.platform import audit, contracts, gateway, policy, prompts, runtime, session_store, vector_store

app = FastAPI(title="Neu.Tail — Experience API", version="2.0.0")
app.mount("/assets", StaticFiles(directory=config.ASSETS_DIR), name="assets")


# --------------------------------------------------------------------------- errors → HTTP
@app.exception_handler(policy.RateLimited)
def _rate_limited(request: Request, exc: policy.RateLimited):
    return JSONResponse(status_code=429, content={"detail": str(exc)})


@app.exception_handler(policy.PolicyDenied)
def _denied(request: Request, exc: policy.PolicyDenied):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(data.DataAccessDenied)
def _data_denied(request: Request, exc: data.DataAccessDenied):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(LookupError)
def _missing(request: Request, exc: LookupError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
def _invalid(request: Request, exc: ValueError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


# --------------------------------------------------------------------------- context
def _ctx(principal: Principal, session_id: str | None = None, as_principal: str | None = None) -> Context:
    ctx = Context(principal=as_principal or principal.name, customer_id=principal.customer_id,
                  session_id=session_id or "default", actor=principal.actor, role=principal.role)
    audit.current_trace.set(ctx.trace_id)
    audit.current_actor.set(ctx.actor)
    return ctx


# --------------------------------------------------------------------------- models
class LoginIn(BaseModel):
    handle: str = Field(min_length=3, max_length=120, description="customer ID or email")


class ChatIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=1000)


class FitIn(BaseModel):
    category: str | None = None
    sku: str | None = None
    customer_note: str | None = None


class OrderIn(BaseModel):
    sku: str
    size: str
    quantity: int = 1
    idempotency_key: str = Field(default_factory=lambda: uuid.uuid4().hex)


class AcceptIn(BaseModel):
    idempotency_key: str = Field(default_factory=lambda: uuid.uuid4().hex)


class ReturnIn(BaseModel):
    order_id: str
    reason_code: str
    condition_grade: str = "A"


class RedeemIn(BaseModel):
    reward_id: str
    idempotency_key: str = Field(default_factory=lambda: uuid.uuid4().hex)


class BrowseIn(BaseModel):
    sku: str


class ResolveIn(BaseModel):
    approve: bool


class ToolInvokeIn(BaseModel):
    args: dict[str, Any] = {}


# --------------------------------------------------------------------------- public
@app.get("/health")
def health() -> dict:
    keys = [k for k in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY") if __import__("os").environ.get(k)]
    return {"status": "ok", "session_backend": session_store.backend(), "vector_backend": vector_store.backend(),
            "live_model_providers": keys, "currency": config.CURRENCY}


@app.get("/demo/customers")
def demo_customers() -> dict:
    """Demo persona picker — names and blurbs only, no contact data."""
    with db.connection() as conn:
        rows = conn.execute(
            """SELECT c.customer_id, c.name, c.persona_blurb, l.tier FROM customers c
               JOIN loyalty l ON l.customer_id = c.customer_id
               WHERE c.persona_blurb != '' ORDER BY c.rowid"""
        ).fetchall()
    return {"customers": [dict(r) for r in rows]}


@app.post("/auth/login")
def login(body: LoginIn) -> dict:
    ctx = Context(principal="storefront_auth", role="system", actor="login")
    audit.current_trace.set(ctx.trace_id)
    identity = runtime.invoke(ctx, "resolve_identity", handle=body.handle)
    return {"token": issue_token(identity["customer_id"]), "customer_id": identity["customer_id"],
            "name": identity["name"], "expires_in": config.TOKEN_TTL_SECONDS}


@app.get("/products")
def products(category: str | None = None, trending: bool = False, on_sale: bool = False, new: bool = False,
             limit: int = 24) -> dict:
    """Public catalogue browse."""
    with data.system_scope():
        rows = catalogue.browse(category, trending=trending, on_sale=on_sale, new_only=new, limit=min(limit, 100))
        stocked = inventory.in_stock([r["sku"] for r in rows])
    return {"results": [r for r in rows if r["sku"] in stocked]}


@app.get("/products/{sku}")
def product(sku: str) -> dict:
    with data.system_scope():
        item = catalogue.get(sku)
        if item is None:
            raise HTTPException(404, f"no such SKU: {sku}")
        sizes = inventory.sizes(sku)
    item["sizes"] = sizes
    item["on_sale"] = bool(item["was_price"] and item["was_price"] > item["price"])
    return item


@app.get("/catalogue/{sku}")
def catalogue_item(sku: str) -> dict:
    return product(sku)


@app.get("/plans")
def plans() -> dict:
    with data.system_scope():
        return {"plans": catalogue.plans()}


# --------------------------------------------------------------------------- shopper
@app.get("/me")
def me(p: Principal = Depends(shopper)) -> dict:
    ctx = _ctx(p)
    segment = runtime.invoke(ctx.as_caller("storefront"), "get_customer_segment")
    loyalty = runtime.invoke(ctx, "get_loyalty_status")
    return {"customer_id": p.customer_id, "segment": segment, "loyalty": loyalty}


@app.post("/chat")
def chat(body: ChatIn, p: Principal = Depends(shopper)) -> dict:
    return orchestrator.handle_message(_ctx(p, body.session_id, as_principal=orchestrator.ORCHESTRATOR), body.message)


@app.get("/recommendations")
def recommendations(session_id: str = "default", category: str | None = None, similar_to: str | None = None,
                    limit: int = 8, p: Principal = Depends(shopper)) -> dict:
    ctx = _ctx(p, session_id, as_principal=orchestrator.ORCHESTRATOR)
    return orchestrator.recommend(ctx, category=category, similar_to=similar_to, top_n=max(1, min(limit, 16)))


@app.post("/events/browse")
def browse_event(body: BrowseIn, p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "log_browse", sku=body.sku)


@app.get("/recently-viewed")
def recently_viewed(p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "recently_viewed")


@app.post("/fit")
def fit(body: FitIn, p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "get_fit_profile", category=body.category, sku=body.sku,
                          customer_note=body.customer_note, log_event=True)


@app.post("/orders")
def place_order(body: OrderIn, p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "commit_order", sku=body.sku, size=body.size, quantity=body.quantity,
                          idempotency_key=f"{p.customer_id}:{body.idempotency_key}")


@app.get("/orders")
def order_history(p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "order_history")


@app.post("/offers/{offer_id}/accept")
def accept_offer(offer_id: str, body: AcceptIn, p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "accept_offer", offer_id=offer_id,
                          idempotency_key=f"{p.customer_id}:{body.idempotency_key}")


@app.post("/returns")
def create_return(body: ReturnIn, p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "record_return", order_id=body.order_id, reason_code=body.reason_code,
                          condition_grade=body.condition_grade)


@app.get("/loyalty")
def loyalty_status(p: Principal = Depends(shopper)) -> dict:
    ctx = _ctx(p)
    return {**runtime.invoke(ctx, "get_loyalty_status"), **runtime.invoke(ctx, "get_challenges")}


@app.post("/loyalty/redeem")
def redeem(body: RedeemIn, p: Principal = Depends(shopper)) -> dict:
    return runtime.invoke(_ctx(p), "redeem_points", reward_id=body.reward_id,
                          idempotency_key=f"{p.customer_id}:{body.idempotency_key}")


# --------------------------------------------------------------------------- reviewer
@app.get("/escalations")
def list_escalations(status: str | None = "pending", p: Principal = Depends(staff)) -> dict:
    require_role(p, "reviewer", "admin")
    with data.system_scope():
        rows = escalations.listing(status or None)
    for row in rows:
        row["customer_ref"] = audit.hash_customer(row["customer_id"])
    return {"escalations": rows}


@app.post("/escalations/{escalation_id}/resolve")
def resolve_escalation(escalation_id: int, body: ResolveIn, p: Principal = Depends(staff)) -> dict:
    require_role(p, "reviewer")
    return runtime.invoke(_ctx(p), "resolve_escalation", escalation_id=escalation_id, approve=body.approve)


# --------------------------------------------------------------------------- admin
@app.get("/audit")
def audit_tail(limit: int = 50, trace_id: str | None = None, p: Principal = Depends(staff)) -> dict:
    require_role(p, "admin")
    return {"entries": audit.recent(min(limit, 500), trace_id)}


@app.get("/admin/traces/{trace_id}")
def trace(trace_id: str, p: Principal = Depends(staff)) -> dict:
    require_role(p, "admin")
    return {"trace_id": trace_id, "entries": audit.recent(trace_id=trace_id)}


@app.get("/admin/outcomes")
def outcomes(p: Principal = Depends(staff)) -> dict:
    require_role(p, "admin")
    return reporting.business_outcomes()


@app.get("/admin/vector_search")
def vector_search(query: str, top_k: int = 10, p: Principal = Depends(staff)) -> dict:
    require_role(p, "admin")
    hits = vector_store.semantic_search(query, top_k=min(top_k, 50), caller="admin")
    with data.system_scope():
        rows = catalogue.many([h["sku"] for h in hits])
    return {"query": query, "backend": vector_store.backend(), "threshold": vector_store.match_threshold(),
            "results": [{**rows[h["sku"]], "score": h["score"]} for h in hits if h["sku"] in rows]}


@app.post("/admin/reindex")
def reindex(p: Principal = Depends(staff)) -> dict:
    require_role(p, "admin")
    return {"indexed": vector_store.build_index(), "backend": vector_store.backend()}


@app.get("/admin/policy")
def policy_view(p: Principal = Depends(staff)) -> dict:
    require_role(p, "admin")
    return {
        "agents": {k: {"name": v[0], "role": v[1]} for k, v in agents.AGENTS.items()},
        "domain_matrix": {a: {d: {"mode": m, "method": meth} for d, (m, meth) in grants.items()}
                          for a, grants in policy.DOMAIN_MATRIX.items()},
        "contracts": [{"name": c.name, "owner": c.owner, "allowed_callers": sorted(c.allowed_callers),
                       "domains": c.domains, "side_effect": c.side_effect, "description": c.description}
                      for c in contracts.all_contracts()],
        "prompts": prompts.catalogue(),
        "models": gateway.models(),
    }


@app.get("/tools")
def list_tools(p: Principal = Depends(staff)) -> dict:
    return {"tools": [c.name for c in contracts.all_contracts() if p.name in c.allowed_callers]}


@app.post("/tools/{tool_name}/invoke")
def invoke_tool(tool_name: str, body: ToolInvokeIn, p: Principal = Depends(staff)) -> dict:
    """Staff/testing pass-through. The caller is the authenticated principal —
    any 'caller' in the body is ignored."""
    args = {k: v for k, v in body.args.items() if k != "caller"}
    return runtime.invoke(_ctx(p), tool_name, **args)
