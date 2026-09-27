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
python3 -m neutail.mcp_server                   # optional — MCP layer over HTTP/SSE, :8765
```

Set `OPENROUTER_API_KEY` or `ANTHROPIC_API_KEY` (OpenRouter is tried
first if both are set) to make routing, MUSE and CONCIERGE call a live
model instead of their deterministic fallbacks — a `.env` file in the
project root works (`neutail/__init__.py` loads it via `python-dotenv`
if installed), or export it directly. Start a Redis server (`brew
install redis && redis-server`) to move session memory off SQLite and
onto Redis with a real TTL. None of this is required — everything falls back
to plain `sqlite3` if it's not there.

## What's here

**Platform** — the two chokepoints from the architecture diagrams, made real:

| File | Role |
|---|---|
| `neutail/db.py`, `schema.sql` | The embedded store — 9 tables + `session_context` (SQLite fallback for short-term memory) + `audit_log` |
| `neutail/contracts.py` | Tool contract registry — name, allowed callers, handler |
| `neutail/policy.py` | Policy engine — default-deny, caller must be on the tool's allow-list |
| `neutail/runtime.py` | Agent Runtime — `invoke_tool()`, the one path every tool call takes; policy-checks and audit-logs every call |
| `neutail/gateway.py` | Model Gateway — tries OpenRouter, then direct Anthropic, then the caller's deterministic fallback; logs the real exception on any failed live attempt instead of swallowing it |
| `neutail/session_store.py` | Session memory (§07 short-term tier) — Redis with a TTL when reachable, else `session_context` in SQLite. Same live/fallback shape as the Model Gateway |
| `neutail/vector_store.py` | Vector database for MUSE's candidate retrieval — Chroma (local ONNX embeddings, persisted to `data/chroma/`) when importable, else a token-overlap index. Same live/fallback shape again — see "Vector search" below |
| `neutail/mcp_server.py` | MCP layer — the database + vector store over HTTP/SSE (`:8765`) for any MCP client. See "MCP layer" below |
| `neutail/response_composer.py` | Merges 2+ per-intent results into one reply for a multi-intent message. Plain module, not an agent — see "Multi-intent classification" below |
| `neutail/orchestrator.py` | Lead Orchestrator — intent routing (live fast-tier call, keyword fallback), the confidence-check self-loop, delegates memory reads/writes to `session_store` |
| `neutail/human_review.py` | Human Review Queue's resolve path — list pending escalations, approve/deny; approving a subscription escalation completes the commit SENTRY paused |
| `neutail/api.py` | Agent Mesh Service (Fig. 04) — the FastAPI app, :8000 |
| `streamlit_app.py` | Streamlit Client (Fig. 04) — storefront (search bar + product grid + detail pages) over `/chat`, review tab over `/escalations`, admin tab over `/admin/outcomes`, :8501 |

**Agents** — each ~40–100 lines, same shape: register a tool contract, implement it, expose `run()` that goes through the runtime.

| Agent | Provides | Notes |
|---|---|---|
| `persona.py` | `get_customer_segment` | Reads CRM + loyalty + behavioural live; segment is never stored |
| `tailor.py` | `get_fit_profile` | Reads returns + fit history; return-risk baseline is computed per-category from seeded orders/returns, not a hardcoded constant; also the tool MUSE calls for the evaluator loop |
| `care.py` | `resolve_contact` | Masks PII; flags upsell eligibility by loyalty tier |
| `sentry.py` | `check_payment_policy` | Approves inside policy bounds, else writes a `pending` row to `escalations` |
| `muse.py` | `rank_products` | Candidate retrieval is `vector_store.semantic_search()` (real embedding-based nearest-neighbor search, not keyword matching); ranks candidates by segment/tier match plus that semantic score, then re-ranks against TAILOR's return-risk before returning; logs every result to `behavioural` so later visits reflect it; caps same-category items at 2 in the final results so one large category can't swamp all slots; pre-filters to 60 candidates before ever building a live-model prompt |
| `concierge.py` | `commit_subscription`, `commit_order` | Requires CARE + SENTRY + TALLY; commits on approval, reports "pending review" on escalation. `commit_order` is a plain product purchase — same policy/points chokepoints, no CARE step, its own SENTRY threshold (§04's Access & Policy table) since a one-time purchase isn't a recurring subscription |
| `tally.py` | `get_loyalty_status`, `earn_points` | UC5, newly in scope. Points = amount × tier multiplier (Bronze 1×, Silver 1.25×, Gold 1.5×, Platinum 2×); tier itself isn't recomputed from points (see below) |

## HTTP API (`neutail/api.py`)

| Endpoint | What it does |
|---|---|
| `POST /chat` | `{session_id, customer_id, message}` → the Orchestrator's `handle_message()` — the demo client's one call |
| `GET /tools` | Lists registered tool contracts, per §05's "declared tool contracts" |
| `POST /tools/{name}/invoke` | `{caller, args}` → straight to `runtime.invoke_tool()` — a policy denial comes back as HTTP 403 with the reason, an unknown tool as 404 |
| `GET /customers/{id}/loyalty` | Read-only tier/points/YTD-spend — never awards points itself |
| `GET /audit?limit=` | Tail of `audit_log` |
| `GET /admin/vector_search?query=&top_k=` | Direct look at `vector_store.semantic_search()` — the same call MUSE's candidate retrieval makes, minus the ranking layers on top |
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

## Recommendations that survive the session

A returning customer should see today's search reflected even after
their session expires (Redis TTL) or they show up on a different
session_id entirely — and that has to come from durable storage, not
session memory, or it wouldn't survive a restart either.

`muse._log_search()` writes every returned SKU to `behavioural` as a
`'search'` event right after ranking. The next call to `_candidates()`
(any session, any day) reads `_recently_engaged_categories()` — categories
browsed or searched in the last `ENGAGEMENT_WINDOW_HOURS` (24h, a rolling
window rather than a calendar day, to sidestep timezone handling for the
demo) — and both the fallback ranker and the live-model prompt give those
categories a boost (`+1.5`, between the tier match's `+2` and the
trending flag's `+1`).

Verified with two separate `/chat` sessions for the same customer, no
state shared between them except SQLite: session A's results include
jeans at position 4 (not yet engaged); session B, a brand-new session_id
right after, already shows jeans as `engaged: true` — because session A's
visit was logged a moment earlier. PERSONA's `preference_tags` picks up
the same rows for free, since it already read `behavioural` for exactly
this purpose (§04) — MUSE was the one place a search wasn't durable yet.

## Default recommendations fire the real pipeline, not a shortcut

`streamlit_app.py`'s `load_recommended()` runs automatically the moment
a shopper lands on a fresh session (and again on "New session") — the
space that used to hold a large logo, or before that a "Popular
searches" chip row, now shows a real "Recommended for you" product grid
without the customer typing anything. It works by sending a synthetic
prompt (`"recommend something for me today"`) through the exact same
`/chat` → Orchestrator → PERSONA → MUSE → TAILOR path a typed search
uses — not a mock, not a cached/canned list. One live call captured
against a fresh session (`audit_log` rows 239-249, `log_id` ascending):

```
239 lead_orchestrator -> model_gateway:fast        (live intent classification)
240 persona_agent     -> get_customer_segment       (local SQL)
241-247 muse_agent     -> get_fit_profile ×7         (evaluator loop, one per candidate category)
248 muse_agent         -> model_gateway:reasoning   (live MUSE ranking call)
249 muse_agent         -> rank_products              (tool-contract wrapper, logs the full result)
```

Two things worth knowing, both confirmed rather than assumed:

- **Nothing is exempt from governance.** All 11 rows land in
  `audit_log` exactly like a typed search would — same
  `runtime.invoke_tool` chokepoint, same `gateway.call_model`
  live/fallback logging. If OpenRouter/Anthropic are unreachable, the
  same deterministic fallback covers this call too, so the grid still
  renders (non-personalized ranking) instead of failing.
- **The cost tradeoff is real, not hypothetical.** This is 2 live model
  API calls (fast-tier classify + reasoning-tier rank, ~15-25s wall
  clock) fired automatically per fresh session, even for a visitor who
  never searches — previously, a live call only happened on an
  explicit search. Negligible at pilot scale (a couple of demo
  shoppers); at real traffic volume it roughly doubles baseline
  model-call volume per visitor. Worth a second look before this goes
  past a pilot, not a problem for the demo as it stands.

As a side effect, the auto-load also writes `last_results` /
`segment` / `last_category_by_sku` to session memory exactly as a real
search would (§07) — so "the second one, in my size" resolves correctly
against the auto-loaded grid too, with no search required first.

## Seed data at build-requirement scale

`neutail/seed.py` generates a population meeting the stated scale target
— 30-50 customers, 1,000+ SKUs, 12 months of transactions, and a returns
history that lands near a 34% baseline — layered *on top of* the original
2 hand-authored "hero" customers and 16 hand-authored SKUs, which are
untouched so the §06 demo script still runs exactly as before.

- **Repeatable, not frozen** — a fixed `RNG_SEED` means `python -m
  neutail.seed` always produces the same database (verified: two fresh
  runs, identical `SUM(points_balance)` and `SUM(ytd_spend)` to the
  cent). "Repeatable" here means deterministic generation, not that
  calendar dates are frozen — transaction dates are still relative to
  when you run it.
- **Computed, not asserted** — `loyalty.points_balance`/`ytd_spend` for
  the generated population come from summing actual generated
  transactions through the same `TIER_MULTIPLIER` table `tally.py` uses
  live, imported directly rather than duplicated. The two hero
  customers keep their original hand-set numbers, since generating
  bulk history for them risked overwriting Priya's specific jeans-return
  anchor row that the demo script depends on.
- **The 34%→10-15% story is simulated causally, not just labeled** —
  `_generate_transactions_and_returns` walks each customer's orders in
  chronological order and draws returns from `GUIDED_RETURN_RISK`
  instead of `TARGET_RETURN_RATE` once a category has fit guidance *at
  that point in time* (`fit_profile.created_at`, tracked per customer as
  generation proceeds). See "Admin — business outcomes" below for what
  this produces and the two dead-end query attempts it took to measure
  it correctly.
- **`tailor.py`'s return-risk baseline is now computed too** —
  `_population_baseline_return_risk()` replaced the hardcoded
  `BASELINE_RETURN_RISK = 0.34` constant with a live per-category query
  (`returns / orders` for that category), falling back to the constant
  only if the DB has no order history yet (e.g. before seeding). §09's
  "34% baseline" stat stops being an asserted number and becomes
  something the seeded data actually produces — verified against a
  manual `SELECT COUNT(*)` for jeans specifically (11/28 = 0.3929,
  matched exactly; this number shifts slightly whenever the generator
  logic changes, since it's genuinely computed rather than fixed).

## Category diversity in MUSE's results

Expanding the catalogue surfaced a real ranking flaw that 16 SKUs never
could: at 1,300+ SKUs, a query for Priya's "something for date night"
returned dresses in all 20 of 20 slots — including under a jeans item
that was *provably scoring higher* per-item (her fit history gives jeans
a 12% return-risk vs. dresses' ~34%). The cause: `trending` is a flat 6%
coin-flip per SKU, and dress simply has ~4x more SKUs than jeans in this
catalogue (244 date-night candidates vs. 62), so it has more *absolute*
trending items (21 vs. 3) — enough alone to fill the results regardless
of any per-item score.

Two things were tried and didn't work, kept here because the failures
were instructive:
1. **Rebalancing which nouns get tagged "date-night"** (e.g. adding
   "Straight Jeans" alongside "Skinny Jeans") helped candidate-pool
   composition but didn't fix the outcome — trending-count imbalance
   scales with raw SKU count regardless of tag percentages.
2. **A first version of the diversity cap** (`_apply_diversity_cap`)
   still produced 100% dresses, because `_fallback_rank` and
   `_rank_products` both independently truncated the ranked list to a
   small pool *before* the cap ran — twice, in two different places —
   leaving nothing but dresses for the cap to choose from. `_fallback_rank`
   now returns every scored candidate, uncapped, and `_rank_products`
   only applies its `response_bound` truncation when `gateway.call_model`
   actually returned a live model's response (`live=True`) — the
   fallback's JSON is valid too, so it silently took the same code path
   as a live response and got re-truncated by it, which is the bug that
   actually mattered.

The fix that stuck: `_apply_diversity_cap(ranked_pool, top_n,
max_per_category=2)` — greedily fills `top_n` from the *full* ranked
pool while capping same-category picks, relaxing the cap only if too
few categories exist to fill the quota otherwise. Verified: Priya's
top 8 now reads dress, dress, **jeans (12% risk)**, accessories,
accessories, top, top, shoes — capped at 2 per category, her fit-history
item visibly surfaced, 0.025s and 9 audit_log rows (unchanged from
before the fix, since this is all in-memory sorting on an
already-fetched candidate list).

## Vector search replaces the hardcoded occasion-tag matcher

Before this, `_candidates()`'s entire query-relevance filter was a
3-phrase `if`/`elif`: `"date"+"night"` → an `occasion_tags LIKE`
lookup, `"work"` or `"gym"` → the same, anything else — including a
plain `"jeans"`, `"shoes"`, or `"gift ideas"` — got **the entire 1,300+
SKU catalogue** handed to the ranker with zero query-relevance signal at
retrieval time, relying entirely on segment/tier/trending scoring
downstream to happen to surface something on-topic.

`neutail/vector_store.py` replaces that retrieval step with real
embedding-based nearest-neighbor search over the catalogue (`name +
category + occasion_tags` as the indexed text per SKU), same live/
fallback shape as `gateway.py` and `session_store.py`:

- **Live** — [Chroma](https://www.trychroma.com/), an embedded vector
  database, persisted to `data/chroma/`, one collection (`catalogue`).
  Embeddings come from Chroma's own bundled local ONNX MiniLM model — no
  API key, no external service; a ~80MB one-time download on first use,
  cached under `~/.cache/chroma/` after that, so it works offline on
  every run after the first.
- **Fallback** — if `chromadb` isn't installed, or initializing it
  throws for any reason, a plain token-overlap index built from the same
  per-SKU text. Cruder (no real semantic generalization — "gift ideas"
  won't specifically find "necklace" the way an embedding would), but
  never breaks search entirely just because an optional dependency is
  missing.

`_candidates()` calls `vector_store.semantic_search(query, top_k=300)`
and fetches exactly those SKUs from `catalogue` — no more full-table
scan for the common case. Every result also carries its `semantic_score`
(0-1) into the candidate dict; `_fallback_rank` folds it into the score
formula (`+2 * semantic_score`, on par with the tier-match bonus), and
the live-model prompt/system-prompt both describe the field so MUSE's
reasoning-tier call weighs it too, not just the deterministic path.

**Verified, on the real seeded 1,300-SKU catalogue** (`vector_store.
semantic_search`, chroma backend, top 15 by score):
- `"jeans"` → all 15 top hits are the `jeans` category.
- `"show me something for date night"` → `top`/`dress` at the top, the
  same categories the old hardcoded tag covered — nothing regressed.
- `"gift ideas"` → `accessories` fills the entire top 15 — a query the
  old matcher had **no path to handle at all** (would've silently fallen
  through to "hand back everything").

Every call also logs to `audit_log` under `tool = "vector_store:search"`
(`caller=muse_agent`, `detail="live=True (chroma) query=... top_k=...
hits=..."`) — the same transparency the Model Gateway gets, visible in
`GET /audit` or the Admin tab right next to the reasoning-tier call it
feeds. `GET /admin/vector_search?query=&top_k=` and the Admin tab's "🧭
Vector search" panel expose the retrieval step directly, without the
ranking layers on top of it, specifically so this can be demoed as its
own piece rather than only inferred from the final product grid.

Seed-time indexing: `neutail.seed.seed()` calls `vector_store.
build_index()` after the catalogue is inserted, so the index is ready
before the demo starts — same `python3 -m neutail.seed` command as
always, no separate step. An existing `data/neutail.db` from before this
feature still works too: `semantic_search()` builds the index lazily on
first call if the collection is empty.

## MCP layer — the database and vector store, over MCP

`neutail/mcp_server.py` exposes 7 read-only tools to any MCP client
(Claude Desktop, Claude Code, a custom agent) over HTTP/SSE on `:8765`,
using `mcp[cli]<2`'s `FastMCP`: `get_customer_segment`,
`get_fit_profile`, `get_loyalty_status`, `search_products` (the vector
store), `get_catalogue_item`, `get_audit_log`, `get_business_outcomes`.
Nothing that commits an order or subscription is exposed.

The point isn't a new capability — `api.py` already exposes all of this
over REST. The point is a second, standard transport onto the *same*
policy-checked data, to demonstrate what MCP actually buys you: three of
the seven tools (`get_customer_segment`, `get_fit_profile`,
`get_loyalty_status`) route through `runtime.invoke_tool()` under caller
identity `"mcp_client"` — the same chokepoint every internal agent call
goes through, not a side door. That identity is explicitly added to
those three tools' `allowed_callers` (persona.py/tailor.py/tally.py);
everything else stays denied by default. Verified: calling
`rank_products` or `commit_order` as `"mcp_client"` raises the same
`PolicyDenied` an unlisted internal caller would get —
`mcp_client cannot call rank_products: not in allowed_callers
['lead_orchestrator', 'muse_agent']` — and both the allowed and the
denied calls land in `audit_log` exactly like any other tool call. The
policy engine doesn't know or care that a caller arrived over MCP
instead of a Python import.

`mcp<2` is pinned deliberately — `mcp` 2.x renamed `FastMCP` to
`MCPServer` with a different API; `1.30.0` is the last release on the
stable v1 shape this file uses.

## Multi-intent classification and ResponseComposer

`orchestrator.classify_intents()` (plural) returns a *list* of
`(intent, confidence)` pairs instead of one. Almost every message still
classifies to exactly one — the entire single-intent path below it is a
verbatim extraction of what `handle_message()` used to do inline
(`_handle_discovery`/`_handle_fit`/`_handle_service`, keyed by intent in
`_HANDLERS`), so nothing about today's demo script changed. What's new
is a message that genuinely asks for two things at once, e.g. **"show me
something for date night, and check my fit for jeans"** — the live
fast-tier classifier (and, mirroring it, the keyword fallback) can now
return `["discovery", "fit"]` instead of forcing a pick. When 2+
classified intents each clear `CONFIDENCE_THRESHOLD`, `handle_message()`
runs each one's existing handler independently — same call each would
make alone, no awareness of the others — and hands the results to
`response_composer.compose()`, which merges them into one reply:
`{"type": "composite", "parts": [...], "summary": "..."}`.

**Deliberately conservative, to avoid regressing the common case.** The
classifier prompt is explicit: return more than one entry *only* when
the message clearly asks for separate things, never split a single
request into artificial parts. The keyword fallback's old behavior for
"matched more than one category" used to mean *ambiguity about a single
intent* (pick the first, at a knocked-down 0.6 confidence); it's now read
as genuine multi-intent evidence instead (each kept, at 0.75) — checked
against every existing demo phrase in `RUNBOOK.md` to confirm none of
them ever accidentally matched two keyword categories, so this is a real
behavior change with zero observed regression risk, not just a
theoretical one.

**Not an agent, on purpose.** `response_composer.py` has no tool
contract, no policy check, no audit-log entry of its own — the same
reasoning that keeps `gateway.py`/`session_store.py`/`vector_store.py` as
plain modules: it touches no database and calls no model, so there's
nothing to policy-gate. Its placement matters more than its size: each
specialist still returns to the Orchestrator directly, exactly as for a
single-intent message — nothing here receives an agent's result before
the Orchestrator does. `handle_message()` collects every qualifying
intent's result itself, then makes *one* call out to `compose()` and
back. A composer that instead sat between the agents and the Orchestrator
(agents calling it directly) would have no precedent anywhere else in
this codebase, where every agent always replies to whoever called it —
this was a real design correction made before writing any code, not an
afterthought.

**Verified**, live and on fallback, discovery+fit and discovery+service:
the compound jeans example above actually resolves the fit part against
the *discovery* part's own just-computed results (`_handle_discovery`
runs first per `_HANDLERS`' iteration order and writes `last_results` to
session memory before `_handle_fit` reads it in the same call) — Priya's
jeans SKU surfaces in the product grid *and* gets her real guided fit
answer (size up, 12% risk), an emergent side effect of handler order, not
special-cased. Full audit trail for a composite call shows both branches'
tool calls in real order (`model_gateway:fast` once, then every tool call
each qualifying handler makes), same transparency as any single-intent
call.

## Getting the live model actually working

Turning on `ANTHROPIC_API_KEY` for real surfaced four separate issues,
none of them visible until someone actually flipped the switch:

1. **`.env` files aren't read by anything.** `os.environ.get(...)`
   only sees real environment variables — a `.env` file just sits there
   unless something loads it. `neutail/__init__.py` now calls
   `load_dotenv()` on import (guarded by `try/except ImportError` so the
   package still works with zero dependencies if `python-dotenv` isn't
   installed — this project has stayed standalone everywhere else, and
   a hard-required import for an optional feature would have broken
   that for anyone running the no-key fallback path).
2. **The `anthropic` package itself was never installed** — only ever
   in `requirements.txt` as an optional line, never actually `pip
   install`ed in this session, since there was no key to test it with
   until now. `gateway.py`'s `except Exception: pass` swallowed the
   resulting `ModuleNotFoundError` identically to "no key set," so it
   looked like a config problem rather than a missing package.
3. **`max_tokens=1024` truncated MUSE's response mid-JSON** at real
   candidate-list scale — a date-night query sends ~674 candidates,
   and asking the model to rank them produced a response that hit
   `stop_reason: max_tokens` before finishing, an unparseable JSON
   fragment that silently fell back to the deterministic ranker.
   Root cause was really the **171,000-character prompt** itself,
   though — the actual fix is `_prefilter_for_prompt()`: candidates are
   diversity-capped down to 60 (`PROMPT_CANDIDATE_LIMIT`, 12 per
   category) before being sent to a live model at all, the same
   `_apply_diversity_cap()` from above at a roomier threshold. `gateway.py`'s
   `max_tokens` also went from 1024 to 4096 as a safety margin on top of that.
4. **`used_live_model` reported whether the *gateway* reached the
   model, not whether the *results* came from it.** When the model's
   response failed to parse (any of the above, or a transient issue —
   see below), `_rank_products` fell back to `_fallback_rank()` but kept
   reporting whatever `live` the gateway returned, which could still be
   `True` — a successful-but-unusable API call looked identical to a
   real live ranking. Fixed by tracking `used_live_results` separately,
   set only when the model's own output actually made it into `results`.

With all four fixed, a live call takes roughly 15-20 seconds (Claude
Opus reasoning over a 60-candidate prompt) and both `gateway.py`'s and
`muse.py`'s exception handlers now log *what* failed to `audit_log`
instead of silently swallowing it — worth checking there first if
`used_live_model` ever comes back `False` unexpectedly again. One
occurrence during testing turned out to be transient (succeeded
identically on retry with no code change) — almost certainly rate
limiting from the burst of calls made while chasing the other three
issues, not a fifth bug.

## OpenRouter as an alternative provider

`call_model()` tries three things in order: OpenRouter (if
`OPENROUTER_API_KEY` is set), then a direct Anthropic call (if
`ANTHROPIC_API_KEY` is set), then the caller's deterministic fallback.
OpenRouter goes through plain `requests` against its OpenAI-compatible
`chat/completions` endpoint — no new SDK, since `requests` was already a
hard dependency for the Streamlit client. Model tiers map to
provider-prefixed slugs in `OPENROUTER_MODEL_POOL`
(`anthropic/claude-opus-4.1`, `anthropic/claude-haiku-4.5`), separate
from `MODEL_POOL`'s direct-Anthropic model IDs, since the two catalogs
don't share names.

This path exists because a real key can fail for reasons that have
nothing to do with the code — walked through three of them in sequence
on one account, each logged with the real error rather than guessed at:
an org-level key not scoped to a workspace (`invalid_request_error`,
fixed with a workspace-scoped key), then insufficient credit balance on
that workspace, then switching providers entirely once OpenRouter had
its own funded key. None of the application code changed between the
first and third attempts — `gateway.py`'s `except Exception` logging
made each cause visible instead of all three looking identical.

Tradeoff worth knowing: OpenRouter adds its own latency on top of the
model call itself — a combined intent-classify + MUSE-rank round trip
ran ~38s over OpenRouter versus ~15-20s for MUSE alone direct to
Anthropic. Both `_log()` calls record which path actually served the
request (`"live=True (openrouter)"` vs. `"live=True (anthropic
direct)"`), visible in `/audit`, so it's never ambiguous which provider
answered a given call.

## Live intent classification

`orchestrator.classify_intents()` used to be pure keyword matching —
deliberate originally, so routing never paid reasoning-model latency.
That meant anything outside a short hardcoded phrase list (`"date
night"`, `"show me"`, `"styling question"`, ...) scored confidence 0.2
and hit the clarify path regardless of how clear the message actually
was — e.g. "help me find an outfit for a candlelit dinner" never matched
anything. Now it calls the fast tier (Haiku) first, with the original
keyword matcher as its deterministic fallback — the same live/fallback
shape as every other model call in this codebase, `gateway.call_model`
under the hood.

Two things this surfaced:

- **Haiku wrapped its JSON in a markdown code fence** (` ```json\n{...}\n``` `)
  on a real call, despite the system prompt saying "reply with ONLY
  JSON" — `json.loads()` on the raw text failed, and (before the
  `used_live_model` honesty fix above existed for this call site too)
  would have silently fallen back while looking successful. Fixed with a
  shared `gateway.extract_json()` that strips a wrapping fence before
  parsing — applied here and, defensively, in `muse.py` too, since it's
  the same underlying model-compliance risk on the same kind of prompt,
  just not yet observed there.
- **A well-calibrated live classifier breaks the old assumption that
  "unknown" always means low confidence.** The keyword matcher's
  "unknown" was a fixed 0.2 by construction; the model confidently
  scores plain small talk like "hello" as `unknown` at ~0.95 — which is
  *more* correct, but would have skipped the friendly clarify message
  (confidence >= threshold) and fallen into the unhandled `"unknown"`
  response type the UI just dumps as raw JSON. Fixed: `handle_message`
  now clarifies whenever `intent == "unknown"`, independent of
  confidence, not just when confidence is low.

Every response from `handle_message` now carries `intent_live_model`
alongside the existing `used_live_model` (MUSE/CONCIERGE's own flag,
unchanged) — the two are independent: a `/chat` call can have live
intent classification but a fallback ranking, or vice versa.

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

The UI surfaces this in the sidebar (tier + points, refreshed every
run) and in the commit confirmation itself (`+90 pts (balance 4,290)`),
both from `GET /customers/{id}/loyalty` and the `points_earned` field
`commit_subscription`/`commit_order`/`resolve_escalation` all return.

## Buying a product — closing a real gap, not just a UI addition

There was no way to actually *buy* a product before — MUSE only ranks
them; CONCIERGE only ever committed subscriptions. `commit_order`
(`concierge.py`) is CONCIERGE's other half of what §04's data-ownership
table already assigned it ("Transactional | Orders ... CONCIERGE"),
newly wired up: looks up the SKU's price, runs the same `check_payment_policy`
→ `earn_points` chokepoints as a subscription, no side door.

One real design decision, not a default: SENTRY's $75 threshold was
sized for subscriptions. Reusing it as-is for orders would send nearly
every premium item ($140-280 in the seeded catalogue) to human review
regardless of customer, which would make "buy something, watch points
land" unreliable as a demo beat — a recurring $75/month commitment and
a one-time purchase are genuinely different risk shapes anyway. Orders
now get their own threshold (`AMOUNT_THRESHOLDS = {"subscription": 75,
"order": 300}` in `sentry.py`), while the first-time-payer check stays
shared — a brand-new customer should still get flagged buying anything,
subscription or not. Verified both paths: Priya (18mo tenure) buying a
$255 dress commits immediately (`+382 pts`, confirmed against the
loyalty endpoint before and after); Jordan (2mo tenure) buying a $49
dress escalates regardless of the modest amount, purely on
first-time-payer — a natural, uncontrived way to hit both outcomes with
the two existing hero customers.

The Streamlit product detail page's **"🛒 Buy now"** button calls this
directly, then `st.rerun()`s so the sidebar's loyalty panel — rendered
earlier in the same script, so it wouldn't otherwise pick up the change
until some *other* interaction triggered a rerun — reflects the new
balance immediately rather than on the next unrelated click.

## The three additions from §03, as code (not just diagram)

- **Evaluator-optimizer loop** — `muse.py`'s `_candidates()` calls `get_fit_profile` for every candidate's category before ranking. Priya's jeans score 12% return-risk (her seeded fit history) against a ~35-40% computed baseline for customers with none, and the ranking reflects it.
- **Reflection / confidence check** — `orchestrator.classify_intents()` (live fast-tier call, keyword matcher as fallback — see "Live intent classification" below) returns a confidence score alongside each intent; below `CONFIDENCE_THRESHOLD` (0.5), or whenever the primary intent is `"unknown"`, `handle_message()` returns a clarifying question instead of routing to a specialist.
- **Human-in-the-loop** — `sentry.py` has no special escalation wire. It writes to `escalations` through the same tool path as any other agent; `concierge.py` reads `approved: False` back and reports `"status": "pending human review"` rather than treating it as a denial.

## Product images

`catalogue.image_url` is a dummy placeholder generated at seed time — a
flat color per category via `placehold.co` (no key, no account), premium
tier gets a small `•` marker in the label. Not real product photography;
just enough for the discovery results to render as a retail-style grid
(`st.columns` + `st.image` in `streamlit_app.py`) instead of a bulleted
list. `_image_url(category, tier)` in `seed.py` is the one place this
would change for a real DAM/CDN — nothing downstream (MUSE, the API, the
UI) cares how the URL was produced, they just pass it through.

## Admin — business outcomes (`GET /admin/outcomes`, 📊 Admin tab)

`neutail/admin.py` computes the numbers §09 states as a target, from the
same tables every agent already reads — no new agent, no tool contract,
just read-only aggregate SQL. Three things worth knowing about what these
numbers do and don't prove, found by actually computing them rather than
asserting them:

- **Return rate, guided vs. baseline** — went through two real fixes to
  get here. First pass: naively splitting orders by "has a `fit_profile`
  entry" measured the *opposite* of the intended story, since
  `fit_profile` rows are generated *from* a customer's own too_small/
  too_large return — counting that triggering return in the "guided"
  bucket guarantees every guided customer >=1 return (58% vs. 17.6%).
  Second pass, excluding the triggering return, still wasn't a real
  measurement: `seed.py`'s generator applied a flat 34% return chance to
  *every* order regardless of `fit_profile`, so no query over that data
  could show a genuine effect — both rates landed near each other
  (18.7% vs. 17.6%) no matter how the SQL was written, because the
  underlying data never encoded guidance *preventing* a return.
  Fixed properly now: `fit_profile.created_at` records when guidance was
  established (schema.sql), `seed.py`'s generator walks each customer's
  orders in chronological order and draws from `GUIDED_RETURN_RISK`
  instead of the baseline rate once a category has guidance *at that
  point in time*, and `admin.py`'s query compares each order's date
  against `fit_profile.created_at` for a real before/after split instead
  of a current-state snapshot. Result on the seeded population: baseline
  **42.8%**, guided **8.2%** — a real, computed effect, not two similar
  numbers dressed up as one.
- **Search-to-purchase** is a proxy, not attributed conversion — share of
  `(customer, category)` search/browse activity with >=1 order in that
  category, because there's no impression-level link from a specific
  MUSE recommendation to a specific later purchase in the schema.
- **Upsell/loyalty numbers** (subscriptions committed, escalations by
  status, points issued) are plain counts — no caveats, they're exactly
  what they say.

## What's not built yet

- No auth on the API — `caller` in `/tools/{name}/invoke` is self-declared by whoever calls it. Fine for a demo where the runtime is the trust boundary; not fine if this endpoint were ever reachable by an untrusted client.
- The Streamlit UI hasn't been browser-tested (no browser tooling was available while building it) — it compiles clean and the server boots with no traceback, and every field it reads matches the API responses verified via curl, but a real click-through is still outstanding.
- Tier promotion from points — see the TALLY section above for why that's a deliberate gap, not an oversight.
- GRADE and SCOUT — out of scope per §01, their signals are pre-seeded directly into `catalogue.trending` and `returns.reason_code`.
- `admin.py`'s "search-to-purchase" is still a proxy (see the Admin section) — there's no impression-level link from a specific MUSE recommendation to a specific later purchase in the schema, so it can only measure category-level search-then-buy, not true attribution.
- `run_demo.py` gets noticeably slower with a live key — every step now makes at least one fast-tier classification call plus, for discovery/service, a reasoning-tier one, so the full script can run past a couple of minutes instead of finishing instantly. Not a bug, just a real cost of moving off the deterministic fallback for the whole walkthrough rather than one call at a time.
