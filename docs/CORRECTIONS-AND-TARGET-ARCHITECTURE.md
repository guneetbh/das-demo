**# Neu.Tail — Corrections & Target Architecture**

Review of the `das-demo` codebase (commit `61f885e`) against the Mission 3 deck

(`docs/deck/T5S7M3-Service-blueprint-commericial-model-v1.pptx` — slides 3, 5–9, 18).

Every finding cites the file and line where it shows up. Severity:

****P0**** = wrong or unsafe behaviour, fix before any demo · ****P1**** = breaks a claim

made in the deck or README · ****P2**** = robustness and quality · ****P3**** = hygiene.

**---**

**## Implementation status (branch `feature/target-architecture`)**

| Items | Status | Where |
|---|---|---|
| #1 offer/accept split | Done | `agents/concierge.py` (`compose_offer` / `accept_offer`), `orchestration/orchestrator._service/_accept` |
| #2 #3 #10 identity & auth | Done | `api/auth.py`, `api/app.py`, `platform/runtime.py` (owner-bound identity, customer binding) |
| #4 server-side pricing & validation | Done | pydantic input models on every contract; `plans` table; price from catalogue/PACE |
| #5 #6 #7 #8 atomicity, idempotency, races | Done | `db.transaction()` unit of work; idempotency keys; CAS on offers/escalations; ledger `UNIQUE(source, ref_id)` |
| #9 audit redaction & role gating | Done | `platform/audit.py::redact`; admin/reviewer keys |
| #11 #12 loyalty intent, TALLY redeem/tier/challenges | Done | `orchestration/intents.py`, `agents/tally.py` |
| #13 entity-aware fit | Done | `orchestration/intents.py` entities; `orchestration/orchestrator._resolve_fit_target`; dependency-ordered dispatch |
| #14 #15 PERSONA publishes a scored segment | Done | `agents/persona.py`; `Context.segment` |
| #16 TAILOR per-customer risk from GRADE | Done | `agents/tailor.py`, `data/fit.guidance_effect` |
| #17 SENTRY risk score | Done | `agents/sentry.py` |
| #18 gateway capability map | Done | `config/models.json`, `platform/gateway.py` (OpenRouter slugs: verify) |
| #19 #36 prompt service, guard prompt, output filters | Done | `neutail/prompts/*.yaml`, `platform/prompts.py` |
| #20 #21 cosine + fingerprint re-index | Done (Chroma path not exercised here, chromadb not installed) | `platform/vector_store.py` |
| #22 #23 MCP external-only, all tools gated | Done | `transports/mcp_server.py`; `mcp` now optional |
| #24 stub agents | Done | `agents/stubs.py` |
| #25 data-access layer enforcing slide 7 | Done | `neutail/data/`, `policy.DOMAIN_MATRIX` |
| #26 #27 #28 audit on errors, single writer, trace id | Done | `platform/audit.py`, `GET /admin/traces/{id}` |
| #29 #30 Redis retry, customer-scoped sessions | Done | `platform/session_store.py` |
| #31 #32 impressions, single stock query | Done | `agents/muse.py`, `data/inventory.in_stock` |
| #33 #34 inventory, schema, indexes | Done | `schema.sql`, `data/inventory.decrement` |
| #35 whole-word ordinals | Done | `orchestration/intents.py` |
| #37 GBP | Done | `config.money`, UI |
| #38 #40 #41 hygiene & docs | Done | `.gitignore`, `docs/archive/`, `README.md`, `docs/RUNBOOK.md`, `docs/ARCHITECTURE.md`, `pyproject.toml`, `apps/` + `scripts/` layout |
| #39 tests | Done (28) | `tests/` |

**---**

**## Part 1 — Corrections**

**### P0 — Unsafe or incorrect behaviour**

| # | Where | Problem | Correction |
|---|---|---|---|
| 1 | `neutail/orchestrator.py:207` → `agents/concierge.py:37-94` | ****A "service" message charges the customer.**** `_handle_service` calls `concierge.run()`, which runs `commit_subscription` straight away. That writes a `transactional` row and awards points with no acceptance step, so typing "I have a styling question" buys a £60 subscription. `run_demo.py:53` relies on this behaviour. | Split CONCIERGE into ****`compose_offer`**** (read-only: offer text, price, plan, and an `offer_id` stored in session memory) and ****`accept_offer(offer_id)`**** (the commit). The orchestrator only ever calls `compose_offer`. The commit runs only after an explicit customer action ("Subscribe" button, or an `accept` intent that refers to the session's `offer_id`). |
| 2 | `neutail/mcp_server.py:80,131…`, `neutail/api.py:47,68-81`, `streamlit_app.py:380-381,411-412` | ****Caller identity is self-declared.**** Any HTTP or MCP client can send `"caller": "lead_orchestrator"` and get write access. The Streamlit UI already pretends to be `concierge_agent` to place orders. The `mcp_server.py` docstring acknowledges this, and it makes the policy engine meaningless. | Derive the caller from ****who is authenticated****, never from the request body. Internal agents get identity from the runtime (the agent module's `AGENT_NAME`, bound when the in-process client starts). External clients (UI, MCP over SSE) authenticate with an API key or JWT that maps to a principal such as `storefront_ui`, `reviewer_ui` or `external_mcp`. Remove `caller` from the public tool signatures. Have the UI call `/orders` (POST), which goes through the orchestrator, rather than `/tools/commit_order/invoke`. |
| 3 | `neutail/api.py:84-87` | ****`/chat` trusts `customer_id` from the client.**** Anyone can act as any customer: see their segment, fit history and masked contact, and place orders for them. | Bind `customer_id` to the authenticated session (a token issued at login, even if the demo only offers a stub "pick a persona" login). Look it up server-side and ignore any value in the body. |
| 4 | `neutail/agents/concierge.py:103`, `:37` (`amount`), `mcp_server.py:278-308` | ****Amounts and quantities are never validated.**** `quantity=-3` gives a negative amount, which falls under SENTRY's threshold, gets approved, and awards negative points. `commit_subscription(amount=0.01)` sidesteps SENTRY the same way. The subscription price is chosen by the caller. | Price comes from the server only: the subscription price from a `plans` table, the order price from `catalogue`. Validate `quantity` as an integer from 1 to 10. Reject `amount <= 0` inside SENTRY too, as defence in depth. |
| 5 | `concierge.py:73-81, 120-128`; `human_review.py:53-77` | ****Commits aren't atomic.**** The order insert and `earn_points` run in separate transactions, and in `human_review` the points step even runs in a different thread and call. If anything fails between the two steps, you get an order with no points, and nothing reconciles it later. | Run "write order, award points, mark the escalation resolved" in ****one DB transaction****. Make TALLY's ledger write a function that takes the open connection, or use an outbox/event that TALLY consumes idempotently. |
| 6 | `concierge.py:72,119`; `sentry.py:53-60` | ****There are no idempotency keys.**** A double-click or client retry creates a second order, a second escalation and a second points award. | Accept an `idempotency_key` on `accept_offer` and `commit_order`. Add a `UNIQUE` constraint on `transactional(idempotency_key)` and `escalations(idempotency_key)`, and return the existing row when a key repeats. |
| 7 | `human_review.py:41-58` | ****The escalation approval races.**** Two reviewers can both pass the `status != 'pending'` read and then both run UPDATE and INSERT, creating two orders. | Use `UPDATE escalations SET status=? … WHERE escalation_id=? AND status='pending'` and continue only if `rowcount == 1`, all inside the same transaction as the order insert. |
| 8 | `agents/tally.py:39-57` | ****Points have a lost-update race.**** The read-modify-write runs across threads, because `mcp_server` uses `asyncio.to_thread`. | Use one statement: `UPDATE loyalty SET points_balance = points_balance + ?, ytd_spend = ytd_spend + ? WHERE customer_id = ? RETURNING …`. Better still, add an append-only `loyalty_ledger` table and compute the balance from it. |
| 9 | `neutail/api.py:90-92`, `mcp_server.py:351-353`, `runtime.py:23` | ****The audit log leaks PII and has no auth.**** `detail` stores the full args and result of every call (customer IDs, fit history, prior returns), and anyone can read it through `/audit` or the MCP `get_audit_log` tool. | Redact before writing: store hashed customer IDs and a result summary, not raw rows. Put `/audit`, `/admin/*`, `/escalations*` and the MCP reporting tools behind an `admin` or `reviewer` role. |
| 10 | `api.py:156-161`, `ResolveEscalationRequest.resolved_by` | ****The reviewer identity is self-declared****, so the HITL approval trail can be forged. | Take `resolved_by` from the authenticated reviewer principal (see #2). |

**### P1 — Gaps against the deck and README**

| # | Where | Problem | Correction |
|---|---|---|---|
| 11 | `orchestrator.py:42-48`, `:218` | ****UC5 Loyalty can't be reached from chat.**** Slide 6 lists intents as **browse · fit · service · loyalty**, but `VALID_INTENTS` has no `loyalty`, so TALLY is only reachable through the sidebar and CONCIERGE. | Add a `loyalty` intent, a `_handle_loyalty` handler (status, points to the next tier, redemption options) and a TALLY tool to match. |
| 12 | `agents/tally.py` | ****TALLY only accrues points.**** The deck gives it gamification, redemption and tier upgrades, with R·W on Loyalty. Tier is never recomputed. | Add `redeem_points` (a SENTRY-style bound check, then a ledger debit), `evaluate_tier` (thresholds on `ytd_spend`, run after each earn) and `get_challenges` (simple streak or badge rules). |
| 13 | `orchestrator.py:187-196`, `:135-162` | ****Fit ignores what the user actually asked about.**** "Check my fit for jeans" with no prior discovery returns an error. In a multi-intent message ("date night ****and**** fit for jeans"), fit sizes discovery's **first result** (probably a dress), not jeans. | Extract an entity before resolving the ordinal. If the message names a category or SKU, use that. Fall back to the ordinal reference into `last_results` only when nothing is named. Ask the live classifier to return `{"intent","confidence","category"?,"ordinal"?}`. |
| 14 | `agents/concierge.py:42-47` | ****CONCIERGE works out the segment itself**** from `loyalty.tier`, copying PERSONA's logic. The deck says PERSONA publishes the segment downstream. | Pass the segment in from the orchestrator's context object (see Part 2 §2.3), or call `get_customer_segment`. Delete the duplicated tier set. |
| 15 | `agents/persona.py:38` | ****Segmentation is a single rule**** (tier ∈ {Gold, Platinum}). `tenure_months` and `preference_tags` are fetched but never used, while the deck promises a real-time classifier on the fast model tier (slide 9). | Score the segment from tier, tenure, 90-day spend and engagement recency. Optionally run a fast-tier model for micro-segments, with this rule as the deterministic fallback. Return `segment`, `confidence` and `signals`. |
| 16 | `agents/tailor.py:34, 156` | ****TAILOR's return risk is a hard-coded constant**** (`GUIDED_RETURN_RISK = 0.12`) for every customer with history. `prior_returns` and `reason_code` (GRADE's output) are fetched but play no part in the risk. | Compute per-customer, per-category risk from that customer's own order/return ratio and their fit-related `reason_code`s, smoothed toward the category baseline (Bayesian shrinkage). This is the GRADE→TAILOR loop the commercial "outcome-based" moat relies on (slide 14). |
| 17 | `agents/sentry.py:35` | ****"First-time payer" is really tenure under 3 months****, not "has never paid". SENTRY doesn't score risk; it compares against two thresholds. | Define first-time payer as `NOT EXISTS (SELECT 1 FROM transactional WHERE customer_id=?)`. Add a simple risk score (amount relative to the customer's average order value, velocity in the last 24h, tenure) and escalate above a score threshold. Return `risk_score` and `reasons`. |
| 18 | `neutail/gateway.py:21-32` | ****The two live paths use different models.**** OpenRouter maps "reasoning" to `claude-opus-4.1`, while direct Anthropic uses `claude-opus-5`, which may not be a valid ID (current IDs include `claude-opus-5-5` and `claude-sonnet-5-5`). Results depend on which key happens to be set. There are no `vision` or `embedding` tiers, though slide 9 lists both. | Keep one capability→model map in config (`config/models.yaml` or env vars) and use it for both providers. Check the IDs against the provider. Add an `embedding` tier that `vector_store` declares, and a `vision` tier stub for GRADE. Reuse one `anthropic.Anthropic` client instead of building one per call (`gateway.py:71`). |
| 19 | `orchestrator.py:50`, `muse.py:35`, `concierge.py:307`, `tailor.py:36` | ****There's no prompt management.**** Prompts are string constants in the agent modules. Slide 8 promises a versioned registry with guard-prompt injection and rollback. | Add `neutail/prompts/` with one YAML file per agent (`id`, `version`, `system`, `guard`) and a `prompt_service.get(agent, version=None)` call. Log the prompt version with every gateway call. Prepend a shared guard prompt (PII, no invented prices, no medical or body judgements). |
| 20 | `neutail/vector_store.py:46, 120` | ****The distance metric doesn't match the docs.**** `reference.md` says cosine similarity, but the collection is created with Chroma's default ****L2**** and scored as `1/(1+d)`. The `>= 0.6` "closely matches" cut-off in `muse.py:155` was tuned for that L2 scale. | Create the collection with `metadata={"hnsw:space": "cosine"}`, score as `1 - distance`, rebuild `data/chroma/`, then re-tune the threshold. |
| 21 | `vector_store.py:73-85` | ****The index never refreshes.**** It is rebuilt only when the collection is empty, so catalogue changes after seeding never reach search. | Store a catalogue fingerprint (row count plus max `updated_at`, or a hash) as collection metadata and rebuild when it changes. Expose `build_index()` from an admin endpoint. |
| 22 | `requirements.txt` ("Optional — mcp"), `neutail/mcp_client.py:31` | ****`mcp` is a hard dependency, labelled optional.**** Every agent imports `mcp_client`, which imports `mcp` at the top level. Without it, nothing imports, which contradicts the README's "zero required dependencies". | Either move `mcp[cli]` into the required block and fix the README, or make internal dispatch transport-agnostic (Part 2 §2.2) so MCP is needed only for the external surface. |
| 23 | `mcp_server.py:206-361` | ****The four "read-only" MCP tools skip policy and audit****, and they run synchronously on the shared event loop. `search_products` runs an ONNX embedding on the loop, which blocks every concurrent `/chat`. | Route them through the same `invoke_tool` chokepoint with their own contracts (`search_catalogue`, `get_catalogue_item`, and so on) and make them `async` plus `to_thread` like the others. |
| 24 | Deck slide 5 vs. code | ****The deck's supporting agents are missing.**** ONEBASKET (identity), SCOUT (trend), PACE (price) and GRADE (returns) don't exist; `trending` is a static seed flag. | You don't need to build them fully, but give each a ****stub agent with a real tool contract**** so the arrows in the deck exist in code: `onebasket.resolve_identity` (map a session or device to `customer_id`), `scout.get_trend_signal(category)` (reads the `trending` column for now), `pace.get_price_context(sku)` (returns the list price and a ±5% band), `grade.record_return(...)` (writes `returns.reason_code`). MUSE and TAILOR then call these instead of reading the columns directly. |
| 25 | Slide 7 data matrix vs. agents | ****Agents read source tables directly****, so the deck's FS / RAG / API / EVT access methods aren't modelled. Every agent opens SQLite and queries whatever it needs. | Add a thin ****data-access layer**** (`neutail/data/`) with one module per domain (crm, loyalty, catalogue, inventory, returns, behavioural, transactional). Each agent declares the domains it may touch, with R/W and method, and the policy engine enforces that matrix. See Part 2 §2.5. |

**### P2 — Robustness and quality**

| # | Where | Problem | Correction |
|---|---|---|---|
| 26 | `runtime.py:22` | When a handler raises, the call is ****never audited****, so only successes and policy denials show up. | Wrap `contract.handler(**kwargs)` in try/except, log `allowed=1, outcome=error, error=…`, then re-raise. |
| 27 | `gateway.py:109`, `vector_store.py:134`, `runtime.py:34` | ****Three separate audit writers****, each opening its own connection, with `vector_store` hard-coding `caller="muse_agent"` even when an admin or MCP client runs the search. | Use one `audit.log(event)` function with a structured schema (`caller`, `kind=tool|model|retrieval|human`, `tool`, `outcome`, `latency_ms`, `trace_id`, `detail`). Pass the real caller through. |
| 28 | Everywhere | ****There's no trace ID****, so a single `/chat` turn can't be followed across orchestrator, tool, model and retrieval rows. That is the "evals & observability plane" on slide 9. | Generate a `trace_id` per request in `api.py` and carry it in a `contextvars.ContextVar` that every audit write reads. Record `latency_ms` too. |
| 29 | `session_store.py:23-36` | ****Redis availability is latched forever**** on the first check, as the README admits. | Retry with backoff (re-check after N seconds following a failure). |
| 30 | `session_store.py:45-65`, `orchestrator.py:141` | ****Session keys aren't scoped to the customer.**** Reusing a `session_id` with a different `customer_id` hands over the previous customer's `last_results`. | Key on `session:{customer_id}:{session_id}:{key}`, or store `customer_id` in the session and reject mismatches. |
| 31 | `muse.py:124-133` | ****Every** **displayed** **result is logged as a `'search'` event****, including the auto-loaded "Recommended for you" grid. Recommendations then boost their own categories the next day, a self-reinforcing loop, and the admin "search→purchase" metric gets inflated. | Log these as `impression` (extend the `event_type` CHECK) and keep `search` for queries the user actually typed. Base `_recently_engaged_categories` on `search`, `browse` and `cart`, not impressions. |
| 32 | `muse.py:97` | `_in_stock` runs one query per candidate (up to 300 queries per search). | Use a single `SELECT sku FROM inventory WHERE sku IN (…) GROUP BY sku HAVING SUM(stock_qty) > 0`. |
| 33 | `concierge.py:97-138`, `human_review.py` | ****Orders don't check or decrement inventory****, and size isn't captured. | Require `size` on `commit_order`. In the same transaction, run `UPDATE inventory SET stock_qty = stock_qty - ? WHERE sku=? AND size=? AND stock_qty >= ?` and fail if no row changes. |
| 34 | `schema.sql` | ****There are no indexes**** on hot paths: `behavioural(customer_id, created_at)`, `returns(customer_id, sku)`, `transactional(customer_id)`, `escalations(status)`. `returns` has no link to the order it came from. | Add the indexes. Add `returns.order_id REFERENCES transactional(order_id)`. Add a `plans` table for subscription pricing (#4). |
| 35 | `orchestrator.py:146-150` | Ordinal matching is substring-based: `"first"` matches "first-time", and "second" can match inside other words. | Match whole words only (`bsecondb`), or have the live classifier return the ordinal (see #13). |
| 36 | `muse.py:217`, `tailor.py:363` | ****User text goes straight into prompts**** (query, customer note) with no guarding. MUSE's output is constrained to known SKUs, but `reason` and TAILOR's `live_guidance` reach the UI unfiltered. | Run user input through the guard prompt (#19), add an output filter (length cap, no URLs, no prices that aren't in the catalogue) and log any rejects. |
| 37 | Code `$` vs. deck `£` | Currency differs (`sentry.py:45`, UI, `run_demo.py`). | Pick GBP to match the deck and the 820K UK loyalty base, and use one `format_money()` helper. |

**### P3 — Repo hygiene**

| # | Where | Problem | Correction |
|---|---|---|---|
| 38 | Repo root | `~$T5S7M3-…pptx` is a PowerPoint ****lock file**** that was committed (now deleted in the working tree). `.claude/scheduled_tasks.lock` was committed and then removed in `61f885e`. | `git rm --cached` both. Add `~$*`, `*.lock` and `.claude/scheduled_tasks.lock` to `.gitignore`. |
| 39 | Repo | ****There are no tests.**** Every "verified" claim in the README was a manual run. | Add `tests/` with pytest: a policy deny matrix, commit idempotency, escalation race, points arithmetic, intent fallback parsing and the diversity cap. Run them against a temporary DB fixture. |
| 40 | `README.md`, `RUNBOOK*.md` (6 files) | Docs have drifted: §-references point to an architecture doc that isn't in the repo, there are six overlapping runbooks, and the docstrings are longer than the code they describe. | Merge into README (run), `docs/ARCHITECTURE.md` (this file's Part 2) and one RUNBOOK (demo script). Cut docstrings to **why**, not **history**. |
| 41 | `seed.py`, DB | Customer emails sit in plain text. Acceptable for synthetic data, but say so. | Add a header comment confirming the data is synthetic. Use `example.com` domains. |

**---**

**## Part 2 — Target architecture**

The aim is to make the code match slides 6, 8 and 9, at demo scale. It stays one

Python process with SQLite and optional Redis and Chroma. Nothing here needs new

infrastructure; it changes where responsibilities live.

**### 2.1 Layer view**

```

┌──────────────────────────────────────────────────────────────────────────────┐

│ CHANNELS                                                                     │

│   Streamlit storefront (shopper)   Streamlit reviewer/admin    External MCP  │

└───────────────┬──────────────────────────────┬───────────────────┬───────────┘

                │ JWT/API-key → principal      │ role=reviewer     │ api-key

┌───────────────▼──────────────────────────────▼───────────────────▼───────────┐

│ EXPERIENCE API  (FastAPI :8000)                         ── auth middleware ── │

│   POST /chat   POST /offers/{id}/accept   POST /orders   GET /loyalty        │

│   GET/POST /escalations (reviewer)   GET /audit, /admin/* (admin)            │

│   → resolves principal + customer_id server-side, mints trace_id             │

└───────────────┬──────────────────────────────────────────────────────────────┘

                │

┌───────────────▼──────────────────────────────────────────────────────────────┐

│ INTENT SENSING & CONTEXT ASSEMBLY                                            │

│   classify → [{intent, confidence, entities{category, sku, ordinal}}]        │

│   intents: discovery · fit · service · loyalty · accept_offer · unknown      │

│   ContextObject{trace_id, customer_id, session_id, segment, last_results,    │

│                 pending_offer, principal}  ← session_store (Redis/SQLite)    │

└───────────────┬──────────────────────────────────────────────────────────────┘

                │

┌───────────────▼──────────────────────────────────────────────────────────────┐

│ LEAD ORCHESTRATOR                                                            │

│   route · decompose (multi-intent, dependency-ordered) · arbitrate · hold ctx│

│   confidence gate → clarify     ResponseComposer (plain module)              │

└───────────────┬──────────────────────────────────────────────────────────────┘

                │  runtime.invoke(ctx, tool, args)   ← only path

┌───────────────▼──────────────────────────────────────────────────────────────┐

│ AGENT MESH                                                                   │

│  UC1 PERSONA ─┐  UC2 MUSE ──► TAILOR (evaluator)   UC4 CARE ► CONCIERGE ► SENTRY│

│   ONEBASKET◄─┘     ▲  ▲                              UC5 TALLY               │

│                 SCOUT PACE        UC3 TAILOR ◄── GRADE (returns reasons)     │

│  (ONEBASKET/SCOUT/PACE/GRADE = thin stubs with real contracts)               │

└───────────────┬──────────────────────────────────────────────────────────────┘

                │

┌───────────────▼──────────────────────────────────────────────────────────────┐

│ AGENT PLATFORM (shared services, not per-agent code)                         │

│   Agent Runtime   – contract registry · identity binding · policy · audit ·  │

│                     errors · idempotency · trace propagation                 │

│   Model Gateway   – capability→model map (reason/fast/vision/embed) ·        │

│                     provider fallback · deterministic fallback · cost/latency│

│   Prompt Service  – versioned YAML per agent · guard prompt · output filters │

│   Memory          – short-term: session_store (Redis TTL / SQLite)           │

│                     long-term: durable tables + preference vectors           │

│   Transports      – in-process (default) · MCP/SSE (external, authenticated) │

├──────────────────────────────────────────────────────────────────────────────┤

│ POLICY & GUARDRAIL PLANE (cross-cutting): caller→tool ACL · agent→domain R/W │

│   matrix · PII redaction · amount/quantity bounds · rate limits              │

│ EVALS & OBSERVABILITY PLANE (cross-cutting): audit_log w/ trace_id, latency, │

│   prompt_version, model, outcome · /admin/traces · offline eval set          │

└───────────────┬──────────────────────────────────────────────────────────────┘

                │

┌───────────────▼──────────────────────────────────────────────────────────────┐

│ DATA ACCESS LAYER  neutail/data/<domain>.py   (method tagged: FS/RAG/API/EVT)│

│   crm · loyalty(ledger) · catalogue · inventory · returns · behavioural ·    │

│   transactional · escalations · plans                                         │

│ STORES: SQLite (system of record) · Chroma (vector, cosine) · Redis (session)│

└──────────────────────────────────────────────────────────────────────────────┘

```

**### 2.2 Agent Runtime — one chokepoint, identity bound by the runtime**

```python

# neutail/runtime.py (shape)

def invoke(ctx: Context, tool: str, **args) -> dict:

    caller = ctx.principal            # bound by runtime/api, never by the payload

    contract = contracts.get(tool)

    contract.validate(args)           # pydantic model per contract (#4)

    policy.check(caller, contract, ctx)   # ACL + domain matrix + rate limit

    with audit.span(ctx, caller, tool, args) as span:   # logs ok / error / denied

        result = contract.handler(ctx, **args)

        span.result(result)

    return result

```

- ****Contracts declare more**** than they do today: `name`, `allowed_callers`,

  `input_model` (pydantic), `domains` (e.g. `{"loyalty": "RW", "transactional": "W"}`),

  `side_effect: bool`, and `idempotent: bool`.

- ****Agent-to-agent calls**** (MUSE→TAILOR, CONCIERGE→SENTRY/TALLY) go through

  `runtime.invoke(ctx.as_caller("concierge_agent"), …)`. The runtime switches identity;

  agents can't choose it.

- ****Transports are adapters.**** The default is a direct in-process call. `mcp_server.py`

  becomes an **external** adapter: it authenticates the API key, maps it to a principal

  (`external_mcp`, read-only by default) and calls `runtime.invoke`. Internal

  dispatch no longer needs an MCP round trip, which removes the hard `mcp`

  dependency (#22) without losing the "MCP for every agent" story, because the

  same contracts are what MCP exposes. If you want to keep internal MCP for the

  demo narrative, leave it as a config flag (`NEUTAIL_INTERNAL_TRANSPORT=mcp|direct`).

**### 2.3 The context object**

One object per turn, passed from agent to agent (slide 8, "threaded between agents"):

```python

@dataclass

class Context:

    trace_id: str

    principal: str          # who is calling right now (switches on agent-to-agent hops)

    customer_id: str        # from auth, not from the request body

    session_id: str

    segment: dict | None    # PERSONA's output, published once per turn (#14)

    last_results: list[dict]

    pending_offer: dict | None   # CONCIERGE offer awaiting acceptance (#1)

```

The orchestrator loads it from `session_store` at the start of a turn and saves it at

the end. Agents read `ctx.segment` instead of re-deriving it.

**### 2.4 Corrected flows**

****Discovery (UC1 + UC2 + UC3 evaluator)****

```

/chat → classify → [discovery]

  → PERSONA.get_customer_segment(ctx)            → ctx.segment

  → MUSE.rank_products(ctx, query)

       ├ vector_store.search(query)   (cosine, audited as kind=retrieval)

       ├ SCOUT.get_trend_signal(categories)   PACE.get_price_context(skus)

       ├ TAILOR.get_fit_profile(ctx, category)   × distinct categories

       ├ gateway.reason(prompt_service.get("muse"))  → fallback ranker

       └ behavioural: 'impression' events (not 'search')       (#31)

  → ctx.last_results = results → reply

```

****Upsell with consent (UC4)**** — replaces the current auto-commit

```

/chat → classify → [service]

  → CARE.resolve_contact(ctx)                     upsell_flag?

  → CONCIERGE.compose_offer(ctx, plan)            read-only; price from `plans`

       → ctx.pending_offer = {offer_id, plan, amount, expires_at}

  ← reply: offer card with [Subscribe] button

POST /offers/{offer_id}/accept  (idempotency_key)

  → CONCIERGE.accept_offer(ctx, offer_id)

       ├ SENTRY.check_payment_policy(ctx, amount, kind)   risk score

       │    ├ approved → ONE TX: transactional + TALLY ledger + tier eval

       │    └ escalate → escalations(pending, idempotency_key)

  ← reply: committed / pending review

```

****Fit (UC3)****

```

/chat → classify → [fit, entities{category?, sku?, ordinal?}]

  → category = entities.category ?? sku→category ?? ctx.last_results[ordinal]

  → TAILOR.get_fit_profile(ctx, category, note?)

       risk = shrink(customer's returns/orders in category, category baseline)

       uses GRADE reason_codes ('too_small', 'too_large', …) for size direction

```

****Loyalty (UC5)**** — new

```

/chat → classify → [loyalty]

  → TALLY.get_loyalty_status(ctx) + TALLY.get_challenges(ctx)

  → reply: tier, points, distance to next tier, redeem options

```

****Human review****

```

POST /escalations/{id}/resolve   (role=reviewer; resolved_by from token)

  → ONE TX: UPDATE … WHERE status='pending' (rowcount==1)

            → transactional + TALLY ledger + inventory decrement

  → audit(kind=human)

```

**### 2.5 Policy model: two layers**

1. ****Tool ACL****, as today but with a real identity: `caller ∈ contract.allowed_callers`.

2. ****Data-domain matrix**** from slide 7, enforced by the data-access layer. Each

   `neutail/data/<domain>.py` function checks `ctx.principal` against this table:

| Agent | CRM | Loyalty | Catalogue | Inventory | Returns | Behavioural | Transactional |
|---|---|---|---|---|---|---|---|
| PERSONA | R | R | | | | R | R |
| MUSE | | | R (RAG) | R | | R·W(impression) | |
| TAILOR | | | R | | R | | R |
| CONCIERGE | R | R | R | | | | W (via accept) |
| TALLY | | R·W | | | | | R |
| CARE | R | R | R | R | R | R | R |
| SENTRY | R | | | | | | R·W(escalations) |
| GRADE (stub) | | | R | | W | | |

On top of that come the guardrails: amount and quantity bounds in contract input

models, PII redaction in `audit.log`, a per-principal rate limit (token bucket in Redis

or memory), and a guard prompt plus output filter in the Prompt Service.

**### 2.6 Schema changes**

```sql

-- new

CREATE TABLE plans (plan TEXT PRIMARY KEY, monthly_price REAL NOT NULL CHECK (monthly_price > 0));

CREATE TABLE loyalty_ledger (entry_id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT NOT NULL,

    delta INTEGER NOT NULL, source TEXT NOT NULL, ref_id TEXT, created_at TEXT DEFAULT (datetime('now')),

    UNIQUE (source, ref_id));                               -- idempotent earn per order

-- altered

ALTER TABLE transactional ADD COLUMN idempotency_key TEXT;  CREATE UNIQUE INDEX ux_tx_idem ON transactional(idempotency_key);

ALTER TABLE transactional ADD COLUMN size TEXT;

ALTER TABLE escalations  ADD COLUMN idempotency_key TEXT;   CREATE UNIQUE INDEX ux_esc_idem ON escalations(idempotency_key);

ALTER TABLE escalations  ADD COLUMN plan TEXT;

ALTER TABLE returns      ADD COLUMN order_id TEXT REFERENCES transactional(order_id);

-- behavioural.event_type CHECK adds 'impression' (needs table rebuild in SQLite)

ALTER TABLE audit_log ADD COLUMN trace_id TEXT;  ADD COLUMN kind TEXT;  ADD COLUMN latency_ms INTEGER;

ALTER TABLE audit_log ADD COLUMN outcome TEXT;   ADD COLUMN prompt_version TEXT;  ADD COLUMN model TEXT;

CREATE INDEX ix_beh_cust_time ON behavioural(customer_id, created_at);

CREATE INDEX ix_ret_cust_sku  ON returns(customer_id, sku);

CREATE INDEX ix_tx_cust       ON transactional(customer_id);

CREATE INDEX ix_esc_status    ON escalations(status);

CREATE INDEX ix_audit_trace   ON audit_log(trace_id);

```

Since this is seeded demo data, the simplest route is to edit `schema.sql` directly

and re-run `python -m neutail.seed` rather than writing migrations.

**### 2.7 Proposed module layout**

```

neutail/

  api.py                 # routes + auth middleware + trace_id

  auth.py                # principals, API keys / JWT, role checks        (new)

  context.py             # Context dataclass, load/save via session_store (new)

  orchestrator.py        # intent → plan → dispatch; no agent logic

  intents.py             # classifier + entity extraction (split out)     (new)

  response_composer.py

  platform/

    runtime.py           # invoke(), identity switching, idempotency

    contracts.py         # + input_model, domains, side_effect

    policy.py            # ACL + domain matrix + rate limit

    audit.py             # single writer, redaction, spans               (new)

    gateway.py           # capability map from config

    prompts.py           # prompt service                                 (new)

    session_store.py

    vector_store.py

  prompts/*.yaml         # versioned prompts + guard.yaml                 (new)

  data/                  # domain DAL, enforces slide-7 matrix            (new)

  agents/

    persona.py muse.py tailor.py care.py concierge.py sentry.py tally.py

    onebasket.py scout.py pace.py grade.py   # thin stubs                  (new)

  transports/

    mcp_server.py        # external adapter, authenticated                (moved)

tests/                                                                      (new)

config/models.yaml                                                          (new)

```

**### 2.8 Suggested implementation order**

1. ****Safety first (P0, about 1 day):**** #1 offer/accept split → #2/#3/#10 identity

   and auth → #4 server-side pricing and validation → #5–#8 transactions,

   idempotency, races → #9 audit redaction and role gating. Add tests alongside each.

2. ****Platform (about 1–2 days):**** runtime `Context` and identity switching,

   single audit writer with `trace_id` (#26–#28), prompt service (#19), gateway

   config (#18), direct transport with MCP as an external adapter (#22, #23).

3. ****Deck parity (about 2 days):**** loyalty intent plus TALLY redeem/tier (#11, #12),

   entity-aware fit (#13), PERSONA scoring (#15), TAILOR per-customer risk with

   GRADE (#16), SENTRY risk score (#17), stub agents (#24), data-access layer (#25).

4. ****Quality (about 1 day):**** vector cosine and re-index (#20, #21), impressions

   (#31), inventory (#33), indexes (#34), currency (#37), hygiene and docs (#38–#41).