# Neu.Tail — agent core

Python + SQLite implementation of the Neu.Tail architecture doc (Mission
#4, Build & Demo) — the original six live agents, plus TALLY (UC5
Loyalty), brought in from out-of-scope once there was a UI to show it on.
Every tool call crosses the same runtime chokepoint the diagrams
describe, on one seeded SQLite file — no external services required to
run it.

```
pip install -r requirements.txt
python3 -m neutail.seed                        # once, creates and populates data/neutail.db
python3 run_demo.py                             # walks the §06 demo script end to end, no server needed

# or the full stack from Fig. 04:
redis-server &                                  # optional — session memory uses it when reachable
uvicorn neutail.api:app --reload --port 8000    # Agent Mesh Service, :8000
streamlit run streamlit_app.py                  # Streamlit Client, :8501 — needs the API running first
```

Set `ANTHROPIC_API_KEY` to make MUSE and CONCIERGE call a live reasoning
model instead of their deterministic fallbacks. Start a Redis server
(`brew install redis && redis-server`) to move session memory off SQLite
and onto Redis with a real TTL. Neither is required — everything falls
back to plain `sqlite3` if it's not there.

## What's here

**Platform** — the two chokepoints from the architecture diagrams, made real:

| File | Role |
|---|---|
| `neutail/db.py`, `schema.sql` | The embedded store — 9 tables + `session_context` (SQLite fallback for short-term memory) + `audit_log` |
| `neutail/contracts.py` | Tool contract registry — name, allowed callers, handler |
| `neutail/policy.py` | Policy engine — default-deny, caller must be on the tool's allow-list |
| `neutail/runtime.py` | Agent Runtime — `invoke_tool()`, the one path every tool call takes; policy-checks and audit-logs every call |
| `neutail/gateway.py` | Model Gateway — routes to a live Claude call when `ANTHROPIC_API_KEY` is set, else a deterministic fallback the caller supplies |
| `neutail/session_store.py` | Session memory (§07 short-term tier) — Redis with a TTL when reachable, else `session_context` in SQLite. Same live/fallback shape as the Model Gateway |
| `neutail/orchestrator.py` | Lead Orchestrator — intent routing, the confidence-check self-loop, delegates memory reads/writes to `session_store` |
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
| `concierge.py` | `commit_subscription` | Requires CARE + SENTRY + TALLY; commits on approval, reports "pending review" on escalation |
| `tally.py` | `get_loyalty_status`, `earn_points` | UC5, newly in scope. Points = amount × tier multiplier (Bronze 1×, Silver 1.25×, Gold 1.5×, Platinum 2×); tier itself isn't recomputed from points (see below) |

## HTTP API (`neutail/api.py`)

| Endpoint | What it does |
|---|---|
| `POST /chat` | `{session_id, customer_id, message}` → the Orchestrator's `handle_message()` — the demo client's one call |
| `GET /tools` | Lists registered tool contracts, per §05's "declared tool contracts" |
| `POST /tools/{name}/invoke` | `{caller, args}` → straight to `runtime.invoke_tool()` — a policy denial comes back as HTTP 403 with the reason, an unknown tool as 404 |
| `GET /customers/{id}/loyalty` | Read-only tier/points/YTD-spend — never awards points itself |
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

## Session memory: Redis or SQLite

`session_store.py` holds only the short-term tier from §07 — `segment`,
`last_results`, `last_category_by_sku` per session, the state that makes
"the second one, in my size" resolve without restating anything. The
durable tier (customers, loyalty, fit_profile, transactional, ...) never
moves; it's what makes cross-session memory (§06 step ⑦) work with no
special-case code — PERSONA and TAILOR just re-read those tables live on
a new session.

On first use per process, `session_store` tries `redis.Redis.ping()`
against `REDIS_URL` (default `redis://localhost:6379/0`); if that
succeeds, every `save`/`load` goes to Redis with a 30-minute TTL
(`SESSION_TTL_SECONDS`) instead of sitting in SQLite forever. If Redis
isn't reachable, or the ping fails, it falls back to the original
`session_context` table — same shape the Model Gateway uses for
reasoning-model calls. `GET /health` reports which one is live
(`session_backend`), and the chat sidebar shows it as a small badge.

Verified both ways: with `redis-server` running, `redis-cli keys
"session:*"` shows the three keys with a 1800s TTL, and a follow-up
message resolves correctly from them; with Redis stopped, the same two
messages produce the identical result via SQLite, no code path changes.

One tradeoff worth naming: availability is checked once and cached for
the process's lifetime. Start Redis *before* `uvicorn`, or restart the
API afterward — it won't notice Redis coming up mid-run. Fine for a
demo; a production version would want to retry a failed connection
instead of latching onto "unavailable" forever.

## TALLY — the loyalty agent

`commit_subscription` calls `earn_points` right after the `transactional`
row lands — both in the direct-approval path (`concierge.py`) and the
human-review approval path (`human_review.py`), so points accrue the
same way regardless of which path completed the order. Points scale with
the purchase amount and the customer's current tier, e.g. a $90 Gold-tier
subscription earns 135 points (`90 × 1.5`).

Deliberately **not** done: tier isn't recomputed from `points_balance`.
A real loyalty program would promote Silver→Gold at some point threshold,
but that creates a feedback loop into PERSONA's segmentation (which reads
`loyalty.tier` directly) and CARE's upsell eligibility gate — tier
changes would silently reclassify a customer's segment mid-session. Worth
building once there's a policy for *when* a tier change should take
effect (immediately vs. next session vs. requiring the same confirmation
SENTRY's threshold gets), not as a side effect of an unrelated feature.

The UI surfaces this in the chat sidebar (tier + points, refreshed every
run) and in the commit confirmation itself (`+90 pts (balance 4,290)`),
both from `GET /customers/{id}/loyalty` and the `points_earned` field
`commit_subscription`/`resolve_escalation` now return.

## The three additions from §03, as code (not just diagram)

- **Evaluator-optimizer loop** — `muse.py`'s `_candidates()` calls `get_fit_profile` for every candidate's category before ranking. In the demo run, jeans score 12% return-risk for Priya (her seeded fit history) against a 34% baseline everywhere else, and the ranking reflects it.
- **Reflection / confidence check** — `orchestrator.classify_intent()` returns a confidence score alongside the intent; below `CONFIDENCE_THRESHOLD` (0.5), `handle_message()` returns a clarifying question instead of routing to a specialist.
- **Human-in-the-loop** — `sentry.py` has no special escalation wire. It writes to `escalations` through the same tool path as any other agent; `concierge.py` reads `approved: False` back and reports `"status": "pending human review"` rather than treating it as a denial.

## What's not built yet

- No auth on the API — `caller` in `/tools/{name}/invoke` is self-declared by whoever calls it. Fine for a demo where the runtime is the trust boundary; not fine if this endpoint were ever reachable by an untrusted client.
- The Streamlit UI hasn't been browser-tested (no browser tooling was available while building it) — it compiles clean and the server boots with no traceback, and every field it reads matches the API responses verified via curl, but a real click-through is still outstanding.
- Tier promotion from points — see the TALLY section above for why that's a deliberate gap, not an oversight.
- GRADE and SCOUT — out of scope per §01, their signals are pre-seeded directly into `catalogue.trending` and `returns.reason_code`.
- Intent classification is keyword-based, not a model call — deliberate, so routing doesn't pay reasoning-model latency and the confidence check stays legible; would be the first thing to swap for a real classifier past the demo stage.
