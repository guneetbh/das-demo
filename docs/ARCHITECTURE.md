**# Neu.Tail — Architecture (as built)**

This is the target architecture from `docs/CORRECTIONS-AND-TARGET-ARCHITECTURE.md` Part 2, as

implemented. It maps onto the Mission 3 deck: slide 6 (blueprint), slide 7 (data universe),

slides 8–9 (agent platform).

**## Layers**

```

Channels         apps/storefront (Streamlit) · reviewer/admin tabs · external MCP clients

                   │ shopper token (customer bound)   │ X-API-Key → principal   │ Bearer MCP key

Experience API   neutail/api/ (app.py, auth.py) — auth → Context(principal, customer_id, trace_id) — no identity from bodies

Intent & context neutail/orchestration/intents.py (fast model + keyword/entity fallback) · neutail/context.py

Orchestrator     neutail/orchestration/orchestrator.py — route · decompose (dependency order) · hold context · confidence gate

Agent mesh       neutail/agents/ — PERSONA MUSE TAILOR CARE CONCIERGE SENTRY TALLY + ONEBASKET SCOUT PACE GRADE

Agent platform   neutail/platform/ — runtime · contracts · policy · audit · gateway · prompts · session_store · vector_store

Data access      neutail/data/<domain>.py — enforces the slide-7 matrix per running contract

Stores           SQLite (system of record) · Chroma (cosine, optional) · Redis (session TTL, optional)

```

**## The one path: `runtime.invoke(ctx, tool, **args)`**

1. Contract lookup (`LookupError` → 404).

2. Policy: caller ∈ `allowed_callers`; rate limit at the system boundary (`PolicyDenied` → 403, `RateLimited` → 429).

3. Customer binding: a shopper-bound context can only act for its own customer; staff/agents must name one.

4. Input validation against the contract's pydantic model (`ValueError` → 400).

5. Handler runs ****as the contract's owner**** inside a data scope limited to the contract's declared

   domains, inside an audit span. Nested calls from a handler therefore go out as that agent.

Every outcome (ok / denied / invalid / error / fallback) is audited with `trace_id`, redacted.

**## Identity**

| Principal | How it's established | Role |
|---|---|---|
| `storefront` | shopper token from `POST /auth/login` (ONEBASKET `resolve_identity`) | shopper, customer-bound |
| `lead_orchestrator` | API sets it for `/chat` and `/recommendations` | shopper, customer-bound |
| `human_reviewer` / `admin` / `external_mcp` | `X-API-Key` (MCP: Bearer key) | reviewer / admin / external, unbound |
| `<agent>_agent` | runtime, while executing that agent's contract | inherited |

**## Money moves only on explicit action**

`compose_offer` (read-only) → customer clicks Subscribe / says "sign me up" → `accept_offer`

→ SENTRY risk score → either one transaction (offer CAS → order → TALLY ledger) or a pending

escalation. `commit_order` and `resolve_escalation` follow the same pattern. All commits take an

idempotency key; offers and escalations change state by compare-and-set; TALLY's ledger is

`UNIQUE(source, ref_id)`.

**## Data-domain matrix (enforced)**

See `neutail/platform/policy.py::DOMAIN_MATRIX`, or ****Admin → Policy**** in the UI. Contracts that

claim a domain their owner isn't granted fail at import; a data call outside the running

contract's grant raises `DataAccessDenied`.

**## Platform services**

- ****Model gateway****: capability → model from `config/models.json` (reasoning `claude-opus-5-5`,

  fast `claude-haiku-4-5`, vision `claude-opus-5-5`, embedding local MiniLM); the same map for

  OpenRouter and Anthropic; server-side refusal fallback on Opus 5.5; deterministic fallback when

  no key or the call fails. OpenRouter slugs are best effort, so check them against OpenRouter's catalogue.

- ****Prompt service****: `neutail/prompts/*.yaml`, versioned, shared guard prompt prepended,

  version recorded on every model call; input cleaning and output filters (no URLs, no invented prices).

- ****Memory****: short-term context object per `(customer, session)` in Redis (TTL) or SQLite;

  long-term = durable tables. Redis is re-probed after failures.

- ****Vector store****: Chroma cosine (`score = 1 − distance`) or token-overlap fallback; rebuilt

  when the catalogue fingerprint changes; `POST /admin/reindex`.

- ****Observability****: `audit_log` with `trace_id`, `kind`, `latency_ms`, `model`, `prompt_version`,

  `outcome`; `GET /admin/traces/{trace_id}`.

**## Agents**

| Agent | Tools | Notes |
|---|---|---|
| PERSONA | `get_customer_segment` | score from tier, tenure, 90-day spend, recency → segment, micro-segment, confidence, signals |
| MUSE | `rank_products`, `log_browse`, `recently_viewed` | semantic candidates → stock → SCOUT/PACE → TAILOR evaluator → rank → diversity cap; impressions vs searches |
| TAILOR | `get_fit_profile` | per-customer risk shrunk to category baseline × measured guidance effect; size direction from GRADE reasons |
| CARE | `resolve_contact`, `order_history` | PII masked; upsell gated on tier ****and**** marketing consent |
| CONCIERGE | `compose_offer`, `accept_offer`, `commit_order`, `resolve_escalation` | the only writer of orders |
| SENTRY | `check_payment_policy` | risk score (amount bound, first-time payer, ×avg order, velocity, tenure); escalates ≥ 0.5 |
| TALLY | `get_loyalty_status`, `earn_points`, `redeem_points`, `get_challenges` | ledger, tier upgrades from YTD spend, rewards, badges |
| ONEBASKET / SCOUT / PACE / GRADE | `resolve_identity` / `get_trend_signal` / `get_price_context` / `record_return` | thin stubs with real contracts |