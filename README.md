# Neu.Tail — agent core

Python + SQLite implementation of the six live agents from the Neu.Tail
architecture doc (Mission #4, Build & Demo). Every tool call crosses the
same runtime chokepoint the diagrams describe, on one seeded SQLite file —
no external services required to run it.

```
pip install -r requirements.txt
python3 -m neutail.seed                        # once, creates and populates data/neutail.db
python3 run_demo.py                             # walks the §06 demo script end to end, no server needed

# or the full stack from Fig. 04:
uvicorn neutail.api:app --reload --port 8000    # Agent Mesh Service, :8000
streamlit run streamlit_app.py                  # Streamlit Client, :8501 — needs the API running first
```

Set `ANTHROPIC_API_KEY` to make MUSE and CONCIERGE call a live reasoning
model instead of their deterministic fallbacks. Nothing else needs it —
the rest of the agent core is plain `sqlite3`.

## What's here

**Platform** — the two chokepoints from the architecture diagrams, made real:

| File | Role |
|---|---|
| `neutail/db.py`, `schema.sql` | The embedded store — 9 tables + `session_context` (short-term memory) + `audit_log` |
| `neutail/contracts.py` | Tool contract registry — name, allowed callers, handler |
| `neutail/policy.py` | Policy engine — default-deny, caller must be on the tool's allow-list |
| `neutail/runtime.py` | Agent Runtime — `invoke_tool()`, the one path every tool call takes; policy-checks and audit-logs every call |
| `neutail/gateway.py` | Model Gateway — routes to a live Claude call when `ANTHROPIC_API_KEY` is set, else a deterministic fallback the caller supplies |
| `neutail/orchestrator.py` | Lead Orchestrator — intent routing, the confidence-check self-loop, session memory |
| `neutail/human_review.py` | Human Review Queue's resolve path — list pending escalations, approve/deny; approving a subscription escalation completes the commit SENTRY paused |
| `neutail/api.py` | Agent Mesh Service (Fig. 04) — the FastAPI app, :8000 |
| `streamlit_app.py` | Streamlit Client (Fig. 04) — chat tab over `/chat`, review tab over `/escalations`, :8501 |

**Agents** — each ~40–100 lines, same shape: register a tool contract, implement it, expose `run()` that goes through the runtime.

| Agent | Provides | Notes |
|---|---|---|
| `persona.py` | `get_customer_segment` | Reads CRM + loyalty + behavioural live; segment is never stored |
| `tailor.py` | `get_fit_profile` | Reads returns + fit history; also the tool MUSE calls for the evaluator loop |
| `care.py` | `resolve_contact` | Masks PII; flags upsell eligibility by loyalty tier |
| `sentry.py` | `check_payment_policy` | Approves inside policy bounds, else writes a `pending` row to `escalations` |
| `muse.py` | `rank_products` | Ranks candidates by segment/tier match, then re-ranks against TAILOR's return-risk before returning |
| `concierge.py` | `commit_subscription` | Requires CARE + SENTRY; commits on approval, reports "pending review" on escalation |

## HTTP API (`neutail/api.py`)

| Endpoint | What it does |
|---|---|
| `POST /chat` | `{session_id, customer_id, message}` → the Orchestrator's `handle_message()` — the demo client's one call |
| `GET /tools` | Lists registered tool contracts, per §05's "declared tool contracts" |
| `POST /tools/{name}/invoke` | `{caller, args}` → straight to `runtime.invoke_tool()` — a policy denial comes back as HTTP 403 with the reason, an unknown tool as 404 |
| `GET /audit?limit=` | Tail of `audit_log` |
| `GET /escalations?status=` | Human Review Queue listing (defaults to `pending`) |
| `POST /escalations/{id}/resolve` | `{approve, resolved_by}` — approving a subscription escalation writes the `transactional` row that was withheld; denying just closes it out |

Every one of these routes back into the same `runtime.invoke_tool()` /
`policy.check_tool_access()` path the CLI demo uses — the API doesn't
duplicate any agent logic, it's a transport in front of what already
existed.

## How the audit log is maintained

`audit_log` (in `schema.sql`) is append-only, written from two places, unconditionally:

- `runtime.invoke_tool()` logs every tool call — `caller`, `tool`, `allowed` (0/1), and a `detail` JSON blob of `{args, result}`, or the denial reason when policy blocks it. This fires inside `invoke_tool` itself, so an agent can't skip it without bypassing the runtime entirely — nothing does.
- `gateway.call_model()` logs every model call the same way, under `tool = "model_gateway:<tier>"`, recording whether it hit the live model or fell back.
- `human_review.resolve_escalation()` logs the reviewer's decision through the same public `runtime.log_event()` hook, so a human's approve/deny shows up in the same trail as an agent's tool call.

Read via `runtime.recent_audit_log(limit)` (or `GET /audit`) — a plain `SELECT ... ORDER BY log_id DESC`. No separate audit service.

## The three additions from §03, as code (not just diagram)

- **Evaluator-optimizer loop** — `muse.py`'s `_candidates()` calls `get_fit_profile` for every candidate's category before ranking. In the demo run, jeans score 12% return-risk for Priya (her seeded fit history) against a 34% baseline everywhere else, and the ranking reflects it.
- **Reflection / confidence check** — `orchestrator.classify_intent()` returns a confidence score alongside the intent; below `CONFIDENCE_THRESHOLD` (0.5), `handle_message()` returns a clarifying question instead of routing to a specialist.
- **Human-in-the-loop** — `sentry.py` has no special escalation wire. It writes to `escalations` through the same tool path as any other agent; `concierge.py` reads `approved: False` back and reports `"status": "pending human review"` rather than treating it as a denial.

## What's not built yet

- No auth on the API — `caller` in `/tools/{name}/invoke` is self-declared by whoever calls it. Fine for a demo where the runtime is the trust boundary; not fine if this endpoint were ever reachable by an untrusted client.
- The Streamlit UI hasn't been browser-tested (no browser tooling was available while building it) — it compiles clean and the server boots with no traceback, and every field it reads matches the API responses verified via curl, but a real click-through is still outstanding.
- GRADE and SCOUT — out of scope per §01, their signals are pre-seeded directly into `catalogue.trending` and `returns.reason_code`.
- No git repository yet — none of this is committed anywhere.
- Intent classification is keyword-based, not a model call — deliberate, so routing doesn't pay reasoning-model latency and the confidence check stays legible; would be the first thing to swap for a real classifier past the demo stage.
