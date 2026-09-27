# Neu.Tail — 15-Minute Demo Script (Live Model)

Runs against a **real reasoning model** throughout — this version exists
because the whole point of this demo is showing live model calls
actually happening, not a fast deterministic simulation of them. That
trade means real waits: a discovery search or a multi-intent query each
take **35-48 seconds** (OpenRouter, reasoning tier, confirmed by timing
it four times while writing this). Budgeted below as two full live
waits, narrated, not skipped past.

If you need speed over authenticity instead, `RUNBOOK.md` documents the
same script on the deterministic fallback path — everything finishes in
under a second, nothing is live. Don't mix the two mid-demo; pick one
before you start.

Two hero customers: **Priya Nair** (`CUST-PRIYA`, affluent, Gold, 18mo)
and **Jordan Lee** (`CUST-JORDAN`, value, Bronze, 2mo).

---

## Pre-flight (T-15 min — the live path needs more lead time than fallback)

**This repo has no `.python-version` file** — plain `python3` on this
machine resolves to system Python 3.9, which can't even parse
`vector_store.py`. Every command below spells out the full interpreter
path; adjust it once if yours differs.

```bash
cd /Users/guneetbhatia/WORK/das-demo
PY=/Users/guneetbhatia/.pyenv/versions/3.13.3/bin/python3.13
$PY --version   # must show 3.13.x
```

**If you're running this from inside a Claude Code/Desktop terminal,
read this before anything else.** That environment pre-sets
`ANTHROPIC_API_KEY=` (empty, for its own unrelated purposes) as a real
OS environment variable — not absent, *empty*. `python-dotenv`'s default
`load_dotenv()` only checks whether a key is *present*, not whether it's
truthy, so it used to silently refuse to load the real key from `.env`
over that empty one — no error, no log line, just a quietly-unusable
Anthropic fallback. Fixed in `neutail/__init__.py` this session (now
applies `.env` values to any key that's still empty *or* absent after
the normal load) — but verify it actually worked in your shell before
you're live in front of people:

```bash
env | grep -i "ANTHROPIC_API_KEY\|OPENROUTER_API_KEY"
# if you see ANTHROPIC_API_KEY= with nothing after the = sign, that's the
# ambient collision described above — confirm the fix picks up the real
# value anyway:
$PY -c "
from neutail import gateway
import os
print('effective ANTHROPIC_API_KEY length:', len(os.environ.get('ANTHROPIC_API_KEY') or ''))
print('effective OPENROUTER_API_KEY length:', len(os.environ.get('OPENROUTER_API_KEY') or ''))
"
# both should print a real length (order of 70-110), not 0
```

**Known account state, worth knowing before you're asked about it live:**
OpenRouter is the working path (`gateway.py` tries it first) — confirmed
minutes before writing this. Direct Anthropic (`gateway.py`'s second
attempt, only reached if OpenRouter fails) currently returns "credit
balance is too low" — a real billing state on that account, not a code
issue. Doesn't block the demo (OpenRouter succeeds first every time), but
if OpenRouter itself has a bad moment live, the audience will see the
*deterministic* fallback kick in next, not a second live provider — know
that going in rather than being surprised by it.

**Don't fire live requests back-to-back in rapid rehearsal** — OpenRouter
returned a real (if transient) `"in_flight_budget_exhausted"` 402 during
testing after several fast consecutive calls, which is exactly what
rehearsing too aggressively right before doing it live risks recreating.
One clean pass through the script below, then stop.

```bash
# clean, known state
rm -f data/neutail.db && $PY -m neutail.seed
# Expect: "Seeded 40 customers, 1300 SKUs, ... Vector index built (chroma backend): 1300 SKUs embedded."

# start the stack — no key-unsetting this time, live is the point
redis-server &
$PY -m uvicorn neutail.api:app --port 8000 &
$PY -m streamlit run streamlit_app.py &
$PY -m neutail.mcp_server &          # external MCP surface, :8765 — needed for the MCP section
```

```bash
curl -s http://127.0.0.1:8000/health
# {"status":"ok","session_backend":"redis"}
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8501
# 200
```

**One real warm-up call, timed, before the room fills — not part of the
15 minutes:**

```bash
time curl -s -X POST http://127.0.0.1:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"warmup","customer_id":"CUST-JORDAN","message":"show me something for date night"}' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print('live:', d['used_live_model'])"
```
Confirm `live: True` and note the actual wall time — if it's wildly
different from ~40s, that's today's real network/model conditions, and
you should adjust your own time budget below accordingly, live, on the
day.

Open **http://localhost:8501** — 🛍️ Shop tab, suggestion chips visible.

---

## Service endpoints (reference)

Four processes started above, four independent surfaces. Handy to have
open in a tab if a question during Q&A needs a live curl instead of a
description.

### Streamlit Client — `:8501`

Single-page UI, no separate sub-routes to call directly.

| Path | Purpose |
|---|---|
| `http://localhost:8501` | 🛍️ Shop (search, product grid, fit check), 🛡️ Human Review (approve/deny escalations), 📊 Admin (outcomes, audit trail, vector-search panel) |

### Agent Mesh Service — FastAPI, `:8000`

The one HTTP surface for the agent mesh. Every route that reaches into
it (`/chat`, `/tools/{name}/invoke`, `/customers/{id}/loyalty`,
`/escalations/{id}/resolve`) goes through `mcp_client` — real MCP,
in-process — not a direct function call.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + which session backend is active (`redis` or `sqlite`) |
| `GET` | `/tools` | Lists the declared tool contracts — what Agent Runtime knows how to invoke |
| `POST` | `/tools/{tool_name}/invoke` | Generic pass-through into the agent mesh — same policy-checked chokepoint every agent uses, reachable over HTTP for testing (body: `{"caller": ..., "args": {...}}`) |
| `POST` | `/chat` | The demo client's one endpoint — `{"session_id", "customer_id", "message"}` in, Orchestrator does the rest |
| `GET` | `/audit` | Tail of `audit_log` — every tool call and model call, most recent first (`?limit=`) |
| `GET` | `/catalogue/{sku}` | Public product lookup — no agent, no policy check |
| `GET` | `/customers/{customer_id}/loyalty` | Read-only loyalty status — does not award points |
| `GET` | `/admin/outcomes` | Business outcomes computed live from the seeded population |
| `GET` | `/admin/vector_search` | Raw `vector_store.semantic_search()` — same call MUSE makes internally, minus ranking (`?query=&top_k=`) |
| `GET` | `/escalations` | Pending (or `?status=`) escalations |
| `POST` | `/escalations/{escalation_id}/resolve` | Approve/deny — `{"approve": bool, "resolved_by": ...}` |

### MCP Server — `:8765`

`python -m neutail.mcp_server` — same tool registry as the Agent Mesh
Service's `/tools/{name}/invoke`, reachable here over the real MCP
protocol (SSE transport) instead of plain HTTP. Default `caller` for
every tool is `"mcp_client"` if not specified — allow-listed on only
the 4 read-only tools; the 9 policy-checked ones need an explicit,
self-declared caller identity (same honest caveat as the REST API).

| Transport endpoint | Purpose |
|---|---|
| `http://127.0.0.1:8765/sse` | MCP SSE connection endpoint — what `demo_mcp_client.py` and any external MCP client (Claude Desktop, etc.) connect to |

| Tool | Kind | Purpose |
|---|---|---|
| `get_customer_segment` | policy-checked | Resolve affluent/value live from CRM + loyalty + behavioural data |
| `get_fit_profile` | policy-checked | Fit/return-risk guidance for a category; optional `customer_note` (free text) triggers one live fast-tier call, omitted stays instant |
| `get_loyalty_status` | policy-checked | Loyalty tier, points balance, YTD spend |
| `earn_points` | policy-checked | Award points for a completed purchase |
| `resolve_contact` | policy-checked | Masks PII, flags upsell eligibility by tier |
| `check_payment_policy` | policy-checked | Approve inside policy bounds, else escalate |
| `rank_products` | policy-checked | Full MUSE discovery pipeline — vector search, evaluator loop, diversity cap, live ranking |
| `commit_subscription` | policy-checked | Commit a subscription — requires CARE + SENTRY + TALLY |
| `commit_order` | policy-checked | Commit a purchase — same chokepoints as a subscription |
| `search_products` | read-only | Semantic product search, ranked SKUs + scores |
| `get_catalogue_item` | read-only | Look up one product by SKU |
| `get_audit_log` | read-only | Tail of the audit trail |
| `get_business_outcomes` | read-only | Same numbers as `/admin/outcomes` |

### Redis — `:6379` (optional)

Not an HTTP service — plain TCP, the `redis-cli` protocol. Session
cache only (`session_store.py`, 30-min TTL); not required — absence
falls back to the SQLite `session_context` table with zero code-path
changes. `GET /health` above reports which backend is actually active.

---

## The script (15:00)

### 0:00–0:45 — Open

**Say:** "This is Neu.Tail — a multi-agent retail assistant, running
against a real reasoning model right now, not a scripted fallback. Seven
specialist agents, a real policy engine, a full audit trail, and — new
this round — real semantic search, multi-intent handling, and an MCP
layer that's now the literal transport for every agent call. You're
going to see it think — a real API call takes 30-45 seconds at the
reasoning tier, and I'm going to talk through exactly what's happening
on the server while we wait, not just stand here."

**Do:** Streamlit open, Shop tab, Priya selected.

---

### 0:45–4:30 — Storefront: watch it actually reason (3:45)

**Say:** "Suggestion chips, nothing auto-loads."

**Do:** Click **"something for the weekend"**.

**Say, immediately, while the spinner is up (you have ~40 seconds —
use them, this is the whole point):**
> "Here's what's happening right now, in order, all of it audit-logged:
> the message just got classified by a live fast-tier model call — that
> part took maybe 2-3 seconds and already finished. Then PERSONA
> resolved her segment from CRM and loyalty data — instant, local SQL.
> Then MUSE ran a real vector search over 1,300 SKUs using a local
> embedding model — also instant, no network call, that's a different
> kind of 'AI' than what we're waiting on now. Then the evaluator loop:
> MUSE asked TAILOR for return-risk on every distinct category in the
> candidate pool — four to seven fast local calls. *Now* — this part —
> is the one live reasoning-tier call: up to 60 pre-filtered candidates,
> with her segment, return-risk, and semantic relevance scores, sent to
> a real model that has to actually reason about which ones to surface
> and write a one-line justification for each. That's the 30-45 seconds."

**Expect:** A relevant, varied grid loads. Point at one product's reason
text — it should read as genuinely generated prose (mentions specific
scores, specific reasoning), not a template.

**Say:** "That's not paraphrased — that sentence was written by the model
a second ago, for this specific product, for her specific profile."

**Do:** Click **"🔧 Behind the scenes"** — `ranking live: True`. Click
into a product → **"📏 Check my fit"** with the note box left blank —
instant (TAILOR doesn't call a model by default, worth saying
explicitly: not everything here is a live call, only the parts that
need to be). Optionally expand **"Anything about your body or fit
preference?"**, click a suggestion chip (e.g. "I have wide calves"),
and check fit again — *that* does trigger one short live fast-tier call
blending the note with her fit history, distinct from MUSE's slower
reasoning-tier ranking call.

---

### 4:30–8:00 — Multi-intent: two agents, one live reasoning call (3:30)

**Say:** "One more live wait, and it's worth it — this message asks for
two genuinely different things at once."

**Do:** Back to results, type:
> `show me something for date night, and check my fit for jeans`

**Say (while it runs — another 30-45s, same reasoning-tier cost as
before, since it's the same MUSE ranking call underneath):**
> "The classifier just returned *two* intents from one live call instead
> of one — discovery and fit — both above the confidence threshold. Right
> now the discovery half is running the identical pipeline you just
> watched. The fit half doesn't need a model at all, so it'll resolve
> instantly once discovery's done — but here's the detail worth catching:
> it's not resolving fit for jeans *in general*, it's resolving it against
> whichever jeans item the discovery half just put in front of her, because
> the discovery handler writes to session memory a few lines before the fit
> handler reads it. Two independent handlers, sharing one session, in the
> order they actually ran."

**Expect:** Summary line — *"Here's some product recommendations, and fit
guidance."* — product grid, then fit guidance for the specific jeans item
in that grid (size-up guidance, ~12% return risk — her real seeded
history, not generic).

**Say:** "A ResponseComposer merged those two independent results into
one reply. Neither PERSONA, MUSE, nor TAILOR know the other ran — the
Orchestrator alone collected both and made one call out to merge them."

---

### 8:00–10:30 — Governance: pause, not rejection (2:30)

**Say:** "Different kind of live call now — short, not a full ranking."

**Do:** Terminal:

```bash
curl -s -X POST http://127.0.0.1:8000/tools/commit_subscription/invoke \
  -H "Content-Type: application/json" \
  -d '{"caller": "lead_orchestrator", "args": {"customer_id": "CUST-PRIYA", "amount": 90.0, "plan": "premium"}}'
```

**Say (this one's fast — ~7-15s, a single generated sentence, not a
60-candidate ranking):** "SENTRY doesn't deny anything outside policy —
it escalates."

**Expect:**
```json
{"committed":false,"escalated":true,"escalation_id":1,"status":"pending human review","reason":"amount $90.00 over $75.00 policy bound for subscription","used_live_model":true,"offer_text":"..."}
```

**Say:** "Even the offer text in that denied-for-now response was live
generated — SENTRY's policy check and CONCIERGE's live offer-writing are
independent of each other."

**Do:** Streamlit → **🛡️ Human Review** tab → **✅ Approve**.

**Expect:** Persisted confirmation, points land immediately.

---

### 10:30–13:00 — MCP: the one section that's fast regardless (2:30)

**Say:** "Deliberate contrast — this next part doesn't touch a model at
all, so it's instant even in a live-model demo. That's worth saying
explicitly: not everything here trades speed for intelligence, only the
parts that need to."

**Do:** Open `docs/architecture-interactive.html`, click component **4**
then **5** (MCP Server, MCP Client).

**Say:** "One tool registry, two transports — external HTTP/SSE, and an
in-process session with zero network hop for internal calls."

**Do:** Terminal:
```bash
/Users/guneetbhatia/.pyenv/versions/3.13.3/bin/python3.13 demo_mcp_client.py
```

**Expect (instant — no live model in this path):**
```
Connected to http://127.0.0.1:8765/sse — 13 tools available: ...
1. get_loyalty_status(CUST-PRIYA) -> {...}
2. rank_products(...) -> isError: True — PolicyDenied: not in allowed_callers [...]
3. rank_products(..., caller='lead_orchestrator') -> isError: False
```

**Say:** "Same policy engine, whether the call came from inside the
FastAPI process a moment ago, or from this terminal just now. The caller
identity is self-declared — same honest caveat our own REST API always
had, worth saying out loud."

---

### 13:00–14:00 — Admin: numbers that are computed (1:00)

**Do:** 📊 Admin tab. Point at **return rate — 42.8% baseline → 8.2%
guided**, computed from the seeded population.

**Do:** Scroll to **Audit trail** — point at several `live=True
(openrouter)` rows from the last 10 minutes, right next to
`vector_store:search` rows (also real, also logged, never a live model
call — local embeddings).

**Say:** "Every wait you sat through in this room is one of these rows.
Nothing here is exempt from the audit trail because it happened to take
40 seconds instead of 40 milliseconds."

---

### 14:00–15:00 — Close (1:00)

**Say:** "What you watched reason for 30-45 seconds twice in this room
was a real model, over real seeded data, with every step — the fast
classify, the local vector search, the evaluator loop, the live ranking
call, the policy check, the audit write — logged in the same table.
Fallback exists for when speed matters more than that; today, showing
you it's real mattered more. Happy to go deeper on any piece afterward —
`docs/architecture-interactive.html` and the sequence diagrams are the
leave-behind."

---

## If something breaks

| Symptom | Do this |
|---|---|
| A wait passes 60s with nothing back | Could be genuinely stuck (rare) or a slow reasoning call (usual max observed: ~48s) — give it to 90s before touching anything |
| Response comes back with `used_live_model: false` unexpectedly | Not a crash — check `GET /audit` immediately, the real reason (rate limit, truncated JSON, `"in_flight_budget_exhausted"`) is logged, never swallowed. Keep going, fallback still gives a correct answer, just say plainly "that one fell back, here's why" if asked |
| `ANTHROPIC_API_KEY` shows length 0 in the pre-flight check | The ambient-empty-var collision described above — confirm you're on the `neutail/__init__.py` fix from this session (`git log -p -- neutail/__init__.py`); if not, `export ANTHROPIC_API_KEY=$(grep ANTHROPIC_API_KEY .env | cut -d= -f2)` manually before starting `uvicorn` |
| `demo_mcp_client.py` hangs or errors | `python -m neutail.mcp_server` isn't running — separate process from `uvicorn` |
| Escalation queue empty when expected | Confirm the `commit_subscription` curl actually printed `escalation_id` — the storefront UI can't request more than $60 on its own |

## After the demo

```bash
rm -f data/neutail.db && /Users/guneetbhatia/.pyenv/versions/3.13.3/bin/python3.13 -m neutail.seed
```
