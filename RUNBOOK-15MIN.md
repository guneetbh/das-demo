# Neu.Tail — 15-Minute Demo Script

A tight, timed subset of `RUNBOOK.md` covering everything built this
build cycle: real semantic search, multi-intent + ResponseComposer,
policy-as-code + human-in-the-loop, and MCP as both an external and
internal transport. Runs on the deterministic **fallback** path — fast
and rehearsable. A live-model bonus beat is called out separately; don't
substitute it into the timed sections, live calls run 15-45s each and
will blow the budget.

Two hero customers carry the script: **Priya Nair** (`CUST-PRIYA`,
affluent, Gold, 18mo) and **Jordan Lee** (`CUST-JORDAN`, value, Bronze,
2mo) — unchanged from the original build.

---

## Pre-flight (T-10 min, before anyone's watching)

**This repo has no `.python-version` file** — plain `python3` on this
machine resolves to system Python 3.9, which can't even parse
`vector_store.py` (a bare `python3 -m neutail.seed` crashes with a
`TypeError` on a `dict[str, Counter] | None` annotation). Every command
below spells out the full interpreter path explicitly rather than
relying on a shell variable — a local `PY=...` variable set in one
terminal window doesn't exist in the next one you open, and you don't
want to discover that mid-demo. Adjust the path once if yours differs,
then copy commands as written.

```bash
cd /Users/guneetbhatia/WORK/das-demo
PY=/Users/guneetbhatia/.pyenv/versions/3.13.3/bin/python3.13

# 0. confirm the interpreter first — must show 3.13.x
$PY --version

# 1. clean, known state
rm -f data/neutail.db && $PY -m neutail.seed
# Expect: "Seeded 40 customers, 1300 SKUs, ... Vector index built (chroma backend): 1300 SKUs embedded."

# 2. fallback mode — fast, deterministic, zero API cost, zero live-call risk
unset OPENROUTER_API_KEY ANTHROPIC_API_KEY

# 3. start the stack (four terminals, or background each — re-set PY in each new terminal)
redis-server &                                       # optional — badge shows "redis" if up, "sqlite" if not, either is fine
$PY -m uvicorn neutail.api:app --port 8000 &          # Agent Mesh Service
$PY -m streamlit run streamlit_app.py &               # opens :8501
$PY -m neutail.mcp_server &                           # external MCP surface, :8765 — needed for the MCP section only
```

```bash
# 4. verify (PY=/Users/guneetbhatia/.pyenv/versions/3.13.3/bin/python3.13 if this is a fresh terminal)
curl -s http://127.0.0.1:8000/health
# {"status":"ok","session_backend":"redis"}   <- or "sqlite", either fine
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8501
# 200
/Users/guneetbhatia/.pyenv/versions/3.13.3/bin/python3.13 demo_mcp_client.py
# should print 13 tools, a successful read, a PolicyDenied, then a second success — if this doesn't
# run clean now, it won't run clean live either; fix it before the room fills
```

Open **http://localhost:8501** — confirm the 🛍️ Shop tab shows the search
bar and a row of suggestion chips (not an auto-loaded product grid).

---

## The script (15:00)

### 0:00–0:45 — Open

**Say:** "This is Neu.Tail — a multi-agent retail assistant. Seven
specialist agents, a real policy engine, a full audit trail, and — new
this round — real semantic search, multi-intent handling, and an MCP
layer that's now the literal transport for every agent call, not just an
external integration. Everything you'll see is live code against a
seeded database, not slides."

**Do:** Streamlit already open, Shop tab, Priya selected in the sidebar.

---

### 0:45–3:30 — Storefront: real search, not string matching (2:45)

**Say:** "Instead of a canned recommendation feed, you get suggestion
chips — nothing loads until you actually search."

**Do:** Click the **"something for the weekend"** chip.

> **Careful — not any chip works here.** On fallback mode, the *intent*
> classifier (a separate, cruder keyword gate than MUSE's vector search)
> only recognizes a handful of exact phrases. "something for the
> weekend" and "show me something for date night" clear it reliably;
> "jeans" / "shoes" / "gift ideas" — despite being *exactly* the free-text
> queries vector search was built to handle — get stuck at "could you say
> more?" on fallback, because they never reach MUSE at all. Verified
> right before writing this: worth fixing in the classifier's keyword
> list separately, not something to discover live.

**Say (while it loads — it's instant on fallback):** "This phrasing used
to be impossible to answer well — the old candidate matcher recognized
exactly three hardcoded phrases and dumped the entire 1,300-SKU catalogue
at the ranker for anything else. This is real vector search now."

**Expect:** A relevant, varied grid — dresses and jeans, not a random
slice of the catalogue.

**Do:** Point at the return-risk badges and category variety (max 2 per
category — the diversity cap). Click **"🔧 Behind the scenes"** — show
`segment: affluent`, routing confidence, `ranking live: False` (fallback,
by design for this run).

**Do:** Click into any product → **"📏 Check my fit."**

**Say:** "TAILOR resolves this live from her actual return history — no
generic size chart."

**Expect:** A specific guidance line + return-risk badge.

---

### 3:30–5:30 — Multi-intent: one message, two agents, one reply (2:00)

**Say:** "Now the new part. This message asks for two genuinely different
things in one turn."

**Do:** Back to results, type into the search bar:
> `show me something for date night, and check my fit for jeans`

**Expect:** A summary line — *"Here's some product recommendations, and
fit guidance."* — then the product grid, then fit guidance below it, in
one response.

**Say:** "The classifier returned two intents instead of one. PERSONA and
MUSE ran independently for the discovery half, TAILOR ran independently
for the fit half — same calls each would make on its own — and a
ResponseComposer merged them. One detail worth pointing at: the fit
answer isn't generic — it resolved against the *jeans item in the grid
above it*, because the discovery handler wrote its results to session
memory a few lines before the fit handler read it. That's not
special-cased, it's what happens when two independent handlers share one
session."

**Do (optional, if the room is technical):** Jump to 📊 Admin tab →
audit trail → point at one `model_gateway:fast` row followed by both
handlers' tool calls in real order.

---

### 5:30–8:00 — Governance: pause, not rejection (2:30)

**Say:** "SENTRY doesn't deny anything outside policy — it escalates to a
human. Here's that path end to end."

**Do:** Switch to a terminal.

```bash
curl -s -X POST http://127.0.0.1:8000/tools/commit_subscription/invoke \
  -H "Content-Type: application/json" \
  -d '{"caller": "lead_orchestrator", "args": {"customer_id": "CUST-PRIYA", "amount": 90.0, "plan": "premium"}}'
```

**Expect:**
```json
{"committed":false,"escalated":true,"escalation_id":1,"status":"pending human review","reason":"amount $90.00 over $75.00 policy bound for subscription"}
```

**Say:** "$90 is over the $75 subscription threshold — SENTRY paused it,
it didn't kill it."

**Do:** Switch to Streamlit → **🛡️ Human Review** tab. Point at the
pending row (amount, reason, timestamp). Click **✅ Approve**.

**Expect:** A persisted confirmation banner — the commit completes and
points land *at that moment*, not when it was first requested.

**Say:** "That approval just triggered TALLY through the exact same
policy-checked path a live commit would — nothing about being
human-approved skips governance."

---

### 8:00–11:30 — MCP: one policy engine, two transports (3:30)

**Say:** "This is the piece that changed the most. There's now a real MCP
server — the same protocol Claude Desktop speaks — and it's not just a
bolt-on integration. It's become the literal internal dispatch path too."

**Do:** Open `docs/architecture-interactive.html` in a browser (or switch
to an already-open tab). Click component **4 (MCP Server)**, then **5
(MCP Client)**.

**Say (reading from the panel, don't just paraphrase — the specific
numbers matter):** "One tool registry, two transports. Externally it's
HTTP/SSE on :8765 — exactly what you're about to see. Internally,
there's no network hop at all — it's an in-process MCP session using the
SDK's own in-memory transport. Same protocol, same messages, zero
sockets."

**Do:** Terminal:

```bash
/Users/guneetbhatia/.pyenv/versions/3.13.3/bin/python3.13 demo_mcp_client.py
```

**Expect (paste-ready, already verified this session):**
```
Connected to http://127.0.0.1:8765/sse — 13 tools available:
  check_payment_policy, commit_order, commit_subscription, earn_points, get_audit_log,
  get_business_outcomes, get_catalogue_item, get_customer_segment, get_fit_profile,
  get_loyalty_status, rank_products, resolve_contact, search_products

1. A read any external caller can make (default caller: mcp_client):
   get_loyalty_status(CUST-PRIYA) -> {"customer_id": "CUST-PRIYA", "tier": "Gold", ...}

2. A write path the SAME external caller is denied — no override, no bypass:
   rank_products(...) -> isError: True
   PolicyDenied: not in allowed_callers ['lead_orchestrator', 'muse_agent']

3. Same tool, self-declared as an internal caller that IS allow-listed —
   proves the policy is checked by identity, not by transport:
   rank_products(..., caller='lead_orchestrator') -> isError: False
```

**Say, pointing at each of the three in turn:** "13 tools — not just
read-only anymore. A plain external caller gets a real product-search
call denied — same policy engine, same allow-list, whether the call came
from inside the FastAPI process or from a terminal a moment ago. Step 3
is the honest caveat: the caller identity is self-declared, same as our
own REST API always was — worth saying out loud, not hiding."

**Do (if time allows, 30s):** Quickly click through components 6, 7, 8
(Policy Engine, Agent Runtime, Audit Log) in the diagram — "every one of
those calls, in both terminals, wrote an audit row through this exact
same function."

---

### 11:30–13:30 — Admin: numbers that are computed, not asserted (2:00)

**Do:** Streamlit → **📊 Admin** tab.

**Say:** "Two honesty checks before anything else — every number here is
computed live from the seeded population, nothing is a hardcoded target."

**Do:** Point at **Return rate — guided vs. baseline**: baseline ~42.8%
→ guided ~8.2%.

**Do:** Scroll to **🧭 Vector search** panel. Type `something cozy for
winter` (a phrase with zero exact keyword overlap in the catalogue) →
**Search the vector index**.

**Expect:** Relevant results with a `backend: chroma` badge.

**Say:** "That's the same retrieval MUSE uses, isolated — no ranking
logic on top, just what the embedding space thinks is closest."

**Do:** Scroll to **Audit trail**, point at a `vector_store:search` row
next to a `model_gateway:*` row — "same transparency, same table, for
every kind of call this system makes."

---

### 13:30–15:00 — Close (1:30)

**Say:** "Everything you saw — the search, the multi-intent merge, the
escalation, the MCP calls — landed in one audit table, policy-checked,
in real call order. Nothing here is exempt from governance because of
which door it came through. Happy to go deeper on any piece — the
architecture diagram and the sequence diagrams in `docs/` are the
leave-behind if you want the wiring-level detail afterward."

---

## Bonus beats, only if time genuinely remains

- **Live model, one query only.** `export OPENROUTER_API_KEY=...` (or
  `ANTHROPIC_API_KEY`), restart `uvicorn`, then try **"gift ideas"** in
  the search bar — the exact query that got stuck at "could you say
  more?" on fallback (see the note in the storefront section) now
  classifies correctly, *and* the reasons become natural language,
  `ranking live: True`. A clean way to show that going live fixes two
  independent things at once. Budget 20-40s of dead air; say so before
  you hit enter.
- **Buy button + points, live in the sidebar.** Any product's detail page
  → **🛒 Buy now** for Priya (commits immediately, order threshold $300)
  vs. Jordan (escalates regardless of price — first-time payer).
- **Cross-session memory.** Sidebar → **New session** → repeat a search
  for the same customer — segment/fit resolve identically, recomputed
  live from durable tables, nothing carried over from the old session.

## If something breaks

| Symptom | Do this |
|---|---|
| `demo_mcp_client.py` hangs or errors | `python3 -m neutail.mcp_server` isn't running — start it, it's a separate process from `uvicorn` |
| `/chat` or the storefront hangs >2s | An API key is still set somewhere — `unset OPENROUTER_API_KEY ANTHROPIC_API_KEY` and restart `uvicorn` |
| "Can't reach the Agent Mesh Service" in Streamlit | `lsof -ti:8000 \| xargs kill; uvicorn neutail.api:app --port 8000 &` |
| Escalation queue empty when expected | Confirm the `commit_subscription` curl actually ran and printed `escalation_id` — the storefront UI itself can't request more than $60 |
| Admin tab vector search shows `backend: fallback` | `chromadb` isn't installed in this Python env — cosmetic for the demo, mention it's the token-overlap fallback, keep going |

## After the demo

```bash
rm -f data/neutail.db && python3 -m neutail.seed   # wipe today's escalations/subscriptions before the next run
```
