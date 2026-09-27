# Neu.Tail — Live Demo Runbook

Runs the five behaviours from the architecture doc's Objective 3 end to
end, on the seeded population: **intent routing, profiling, discovery,
fit, and upsell** — plus the two things that make the numbers credible
rather than asserted: the audit trail and the business-outcomes
dashboard. This is an operational script for demo day, not developer
docs — see `README.md` for how any of this actually works.

Two hero customers carry the whole script and are hand-authored, never
touched by the bulk seed generator:

| | Priya Nair (`CUST-PRIYA`) | Jordan Lee (`CUST-JORDAN`) |
|---|---|---|
| Segment | affluent · Gold · 18 months | value · Bronze · 2 months |
| Fit history | jeans run small (seeded return) | none — no-history fallback |
| Upsell-eligible | yes (Gold tier) | no (Bronze tier) |

---

## Before you start (T-15 min)

**1. Reset to a known, clean state.** Always do this right before the
demo, not the night before — it wipes anything a rehearsal run wrote
(escalations, subscriptions, audit rows):
```bash
cd /Users/guneetbhatia/WORK/das-demo
rm -f data/neutail.db && python3 -m neutail.seed
```
Expect: `Seeded 40 customers, 1300 SKUs, ... 372 orders over 12 months.`
`Returns: 138 (target rate 34%, achieved 37.1%)`. Numbers may drift
slightly if the generator changes, but customer/SKU counts and the
±1300 SKU scale should always be there.

**2. Decide: live model or fallback — see the callout below before
picking.**

**3. Start the stack** (each in its own terminal, or backgrounded):
```bash
redis-server &                                # optional — session memory badge shows "redis" if up, "sqlite" if not
uvicorn neutail.api:app --port 8000 &
streamlit run streamlit_app.py                # opens http://localhost:8501
```

**4. Verify before anyone's watching:**
```bash
curl -s http://localhost:8000/health
# {"status":"ok","session_backend":"redis"}  <- or "sqlite", either is fine
```
Open http://localhost:8501 and confirm the **🛍️ Shop** tab loads —
search bar at top, "Popular searches" suggestion chips below it, the
Priya/Jordan selector and a loyalty panel in the sidebar.

**5. Have a second window ready** on `http://localhost:8000/docs`
(Swagger UI) — one step below needs it, since a $75+ offer (to trigger
an escalation) isn't reachable from the storefront UI, which only ever
requests the standard $60 offer (see step 5).

---

> ### Live model or fallback? Pick deliberately, don't default.
>
> **Fallback (no `OPENROUTER_API_KEY`/`ANTHROPIC_API_KEY` set, or unset
> them for this run)** — every response in under a second, fully
> deterministic (same script produces the same SKUs every time,
> rehearsable), zero cost, zero risk of an API hiccup mid-presentation.
> The mechanical reason text ("premium pick for the affluent segment,
> trending; return-risk 35%") is less impressive prose but demonstrates
> every architectural piece just as completely — the evaluator loop, the
> diversity cap, the policy engine, TALLY, all of it are independent of
> which ranker produced the list.
>
> **Live (a working key set)** — genuine natural-language reasoning per
> recommendation, a real reflection/confidence check instead of keyword
> matching. Costs real API credit and takes **15-40 seconds per
> model-touching step** (confirmed: ~38s for a combined intent-classify +
> discovery-rank round trip over OpenRouter, ~15-20s direct to
> Anthropic) — budget accordingly, and know that getting a key working
> at all took three real, sequential account issues to resolve last time
> (workspace scoping, then credit balance, then a provider switch) before
> it worked reliably. If you haven't rehearsed the live path within the
> last day, don't debut it live in front of an audience.
>
> **Recommended for a first stakeholder demo:** run the whole script on
> **fallback** for a reliable, fast, fully-rehearsed walkthrough, then —
> only if time and confidence allow — repeat *one* discovery query live
> near the end as a "and here's what it sounds like with a real
> reasoning model" bonus beat, not the backbone of the demo.

---

## The script

Do this in order; each step maps to one of the five functionalities and
closes with a Mission #3 diagram reference (§06 of the architecture
doc) if anyone asks "where's this documented."

### 1 — Intent routing & the confidence check *(reflection, §03)*

In the **🛍️ Shop** tab's search bar (either customer), type something
that's deliberately unclear:
> `hi`

**Expect:** a warning banner — *"Could you say a bit more? I want to
route this to the right specialist (best guess: unknown)."* — this is
the self-check firing, not a bug: the Orchestrator won't commit to a
specialist below a confidence threshold. (On fallback, "unknown" is
always low confidence by construction; live, the model can be *highly*
confident something's unclear — either way, `unknown` always clarifies.)

Then search for real:
> `show me something for date night`

**Expect:** a product results grid, not a clarify banner — routing
worked. Point at the collapsed **"🔧 Behind the scenes"** expander below
the grid — that's where the segment/confidence/live-model flags live now,
deliberately out of the way of what a real storefront would show a
customer.

### 2 — Profiling *(PERSONA, UC1)*

Open the "🔧 Behind the scenes" expander: `segment: affluent` for Priya,
`segment: value` for Jordan — resolved live from CRM + loyalty tier,
not stored or hand-typed. Switch customers in the sidebar and re-run
the same search to show it side by side.

### 3 — Discovery *(MUSE, UC2 — evaluator loop + diversity cap + vector search)*

Same search as above (`show me something for date night`) for **Priya**
specifically. In the product grid, point out:

- **Candidate retrieval is a real vector database, not keyword
  matching.** Try a query the old build couldn't handle at all, like
  `gift ideas` or just `jeans` — both now return relevant, on-topic
  results (accessories, and jeans respectively) because
  `vector_store.py` does an embedding-based nearest-neighbor search over
  the catalogue (Chroma, local model, no API key) instead of matching
  against three hardcoded phrases. See the "🔧 Behind the scenes"
  expander's segment/confidence line, or `GET /audit` for a
  `vector_store:search` row right before the reasoning-tier call — same
  transparency as every other model/tool call in this system.
- **Category variety** — no more than 2 items from any single category
  (the diversity cap — without it, at 1,300+ SKUs one category can
  swamp every slot on raw item count alone, a real bug found and fixed
  during this build). This holds on both fallback and live.
- **A jeans item with a visibly lower return-risk** than the rest
  (~12% vs. ~35-40% elsewhere) — that's the evaluator loop: MUSE called
  TAILOR's fit service for every candidate's category before ranking.
  **Reliable every time on fallback** (deterministic scoring, verified
  repeatedly); **not guaranteed live** — the model sees the same
  return_risk field and is instructed to weigh it, but it's making its
  own judgment call and can choose dress/jacket over jeans anyway
  (confirmed: it did, in a fresh run while writing this). If you need
  this specific beat to land every time, run it on fallback, or check
  the response first and re-ask if jeans didn't surface.

### 4 — Fit & size guidance *(TAILOR, UC3)*

**First, look at what step 3's grid actually returned.** MUSE doesn't
guarantee a jeans item lands in Priya's top 4 (diversity cap +
live-model judgment mean the mix varies; confirmed missing entirely in
more than one validation run). Two ways to show her guided-fit story, in
order of reliability:

**A — guaranteed, via the API** (use this if you need it to land every
time, e.g. presenting to an audience with no room for "let me try that
again"):
```bash
curl -s -X POST http://localhost:8000/tools/get_fit_profile/invoke \
  -H "Content-Type: application/json" \
  -d '{"caller":"tailor_agent","args":{"customer_id":"CUST-PRIYA","category":"jeans"}}'
```
**Expect:** `"has_history": true, "guidance": "size up from M — past returns show this category runs small", "return_risk": 0.12`. Bypasses MUSE's ranking entirely — same tool TAILOR always uses, just not gated on whether jeans happened to rank.

**B — in the UI, if a jeans item did land in Priya's grid:** click into
its **product detail page**, then click **"📏 Check my fit."** This calls
TAILOR directly for that exact product's category — no ordinal reference
("the second one") involved at all, since you're already looking at the
specific product. That ambiguity only existed in the old chat-transcript
UI; clicking a product sidesteps it entirely.

Either way, then switch to **Jordan**, open any product's detail page,
and click "Check my fit" — expect the **graceful no-history fallback**
("no fit history yet — using the standard size guide") regardless of
which product, since she has no fit_profile row at all. Same code path
as Priya's guided answer; the seeded data is the only difference.

### 5 — Upsell, including human-in-the-loop *(CONCIERGE/CARE/SENTRY, UC4)*

Two equivalent ways to trigger this, as **Priya**: type `I have a
styling question` into the search bar, or open any product's detail
page and click **"💬 Talk to a stylist."** Both call the exact same
service-intent flow.

**Expect:** an offer, approved inside policy ($60, well under the
$75/first-time-payer thresholds), a committed subscription, and points
earned in the sidebar loyalty panel (`+90 pts` for Priya's Gold 1.5×
multiplier).

**Neither UI path can request more than the standard $60 offer, so
neither can trip an escalation on its own.** To show the
human-in-the-loop path, switch to the Swagger UI (`localhost:8000/docs`)
→ `POST /tools/commit_subscription/invoke` → Try it out:
```json
{"caller": "lead_orchestrator", "args": {"customer_id": "CUST-PRIYA", "amount": 90.0, "plan": "premium"}}
```
**Expect:** `"committed": false, "escalated": true` — SENTRY didn't deny
it, it routed to a human. Go to the **🛡️ Human Review** tab in
Streamlit, find the pending row, and click **Approve** — the commit
completes and points post *at that moment*, not when it was first
requested. This is the point to make explicitly: the escalation isn't
a rejection, it's a pause.

### Bonus beats, if time allows

- **Buy a product, watch points land** — open any product's detail
  page and click **"🛒 Buy now."** For **Priya**, this commits
  immediately (SENTRY's order threshold is $300, well above the
  premium catalogue's ~$280 ceiling) — the sidebar's points balance
  updates right there, no page navigation needed. For **Jordan**, the
  *same* button on *any* product escalates instead, regardless of
  price — she's a first-time payer (2 months' tenure), and that check
  applies to purchases the same as subscriptions. Two customers, two
  outcomes, same button — a clean way to show SENTRY's policy isn't
  subscription-specific.
  Then close the loop on Jordan's: go to **🛡️ Human Review**, find the
  row (it now shows the SKU alongside amount/reason for order-type
  escalations, not just subscriptions), and **Approve** — a persisted
  banner confirms the order and points, the same completion CONCIERGE
  would have done directly if SENTRY hadn't paused it. This exact path
  only fully works as of the latest fix — `resolve_escalation()` used
  to only know how to complete a *subscription* escalation, so
  approving an order-type one used to silently do nothing (status
  flipped to "approved," no purchase, no points) until that gap closed.
- **Recent searches** — after a couple of searches, scroll to the
  bottom of the results page. A retail-site-style footer strip of past
  queries appears; clicking one re-runs it instantly. Session-scoped
  (clears on "New session"), separate from the durable `behavioural`
  history PERSONA/MUSE read — this is just a UI convenience, not part
  of the personalization story.
- **Cross-session memory** — click "New session" in the sidebar
  (simulates a return visit with no shared state) and re-run Priya's
  search. Segment and fit signal are already there — recomputed live
  from durable tables, not carried over from the old session, which is
  what makes it survive a Redis restart too.
- **TALLY / loyalty** — point at the sidebar panel before and after the
  step 5 commit; the balance moves by exactly `amount × tier
  multiplier`, visible in real time.
- **The vector database, isolated from the rest of the pipeline** — in
  the **📊 Admin** tab, "🧭 Vector search" panel: type any free-text
  query (try `gift ideas`, `jeans`, `something cozy for winter`) and hit
  **Search the vector index**. This calls `vector_store.semantic_search()`
  directly — the same retrieval MUSE's discovery uses — with no
  segment/tier/diversity ranking on top, so what comes back is purely
  "what does the embedding space think is closest to this query." Good
  for showing the vector database as its own piece when someone asks
  "wait, how does the semantic matching actually work?" instead of only
  inferring it from the final product grid. The badge next to the query
  shows which backend answered (`chroma` vs. `fallback`), same
  live/fallback transparency as the session-memory badge.
- **MCP: an external agent talking to our database directly** — start
  the MCP layer (`python3 -m neutail.mcp_server`, serves HTTP/SSE on
  `:8765`) and connect an MCP client (Claude Desktop, Claude Code, or the
  small script below) to it. Ask it something like "what's Priya's
  loyalty tier, and does she have any jeans return history?" and watch it
  call `get_loyalty_status` and `get_fit_profile` live against the real
  seeded DB — tool calls visible in the client's own UI. Then show the
  policy side: any tool NOT explicitly allow-listed for the `mcp_client`
  caller gets denied exactly like an unlisted internal agent would —
  ```bash
  python3 -c "
  from neutail import runtime, policy
  from neutail.agents import muse
  try:
      runtime.invoke_tool('mcp_client', 'rank_products', customer_id='CUST-PRIYA', segment='affluent', query='jeans')
  except policy.PolicyDenied as e:
      print('denied:', e)
  "
  ```
  and both the allowed and denied calls show up in `GET /audit` under
  `caller=mcp_client`, same as anything else. The point: MCP is just
  another transport onto the same policy-checked runtime, not a bypass.
- **The Admin tab** — the business-outcome numbers, computed from the
  seeded population, not asserted: baseline return rate **42.8%**
  drops to **8.2%** once fit guidance exists for that category — a
  real, queryable effect (`GET /admin/outcomes`), not the "34%→10-15%"
  figure quoted as a constant. Mention the one honest gap while you're
  there: search-to-purchase currently reads 0% on the freshly seeded
  data (no purchases yet correlate with searches until the demo itself
  generates some) — say that plainly rather than let a flat zero look
  like something's broken.
- **The audit trail** — also in the **📊 Admin** tab now (moved off the
  main shop view to keep the storefront looking like a storefront), or
  `GET /audit?limit=20` directly. Every step above is a row here,
  policy-checked. This is the answer to "how do we know this is
  actually enforcing anything" —
  point at a `resolve_escalation` row right next to the `commit_subscription`
  it completed.

---

## Architecture walkthrough — trace one request, and why it's built this way

The five-functionality script above proves the demo *works*. This is
for when the audience is technical and the question is "why is it built
this way" — one concrete request, traced hop by hop through real code,
with the actual `audit_log` rows as evidence, not a narrated diagram.

**Run this, then pull the trail immediately after:**
```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"arch-demo","customer_id":"CUST-PRIYA","message":"show me something for date night"}' > /dev/null
curl -s "http://localhost:8000/audit?limit=10" | python3 -m json.tool
```

**What actually comes back** (captured live, on fallback mode — with a
different candidate mix live, but the same shape):
```
1  lead_orchestrator -> model_gateway:fast        (allowed)
2  persona_agent     -> get_customer_segment      (allowed)
3  muse_agent        -> get_fit_profile           (allowed)
4  muse_agent        -> get_fit_profile           (allowed)
5  muse_agent        -> get_fit_profile           (allowed)
6  muse_agent        -> get_fit_profile           (allowed)
7  muse_agent        -> get_fit_profile           (allowed)
8  muse_agent        -> get_fit_profile           (allowed)
9  muse_agent        -> model_gateway:reasoning   (allowed)
10 muse_agent        -> rank_products             (allowed)
```

Walk the audience through it in this order, pointing at each row:

**1. Browser → Streamlit → `POST /chat` → FastAPI.** The UI never
touches an agent module directly — it only knows one HTTP contract
(`neutail/api.py`'s `/chat`). *Why:* the client is swappable — CLI,
mobile app, a different UI entirely — with zero backend changes. This
is Fig. 04's Streamlit-Client-to-Agent-Mesh-Service edge, not
decoration.

**2. FastAPI → `orchestrator.handle_message()`.** One function is the
entry point for every customer message, regardless of what it turns
out to be about. *Why:* keeps "what does this message mean" owned by
the Orchestrator, not the transport layer — the Lead Orchestrator's
job per the architecture doc is exactly "routes · decomposes ·
arbitrates · holds context," nothing upstream of it should need to
know what an intent even is.

**3. Row 1 — `model_gateway:fast`.** Intent classification, live model
first, keyword matcher as fallback. *Why it's a live call at all:* the
old pure-keyword version only matched a handful of exact phrases;
*why it still has a fallback:* routing is the one thing that must never
go down just because a model API hiccups — confirmed necessary this
build, when getting a *working* key took three separate real account
issues to resolve.

**4. The confidence check (no row — it's a branch, not a call).** Below
threshold, or intent is `"unknown"`, the Orchestrator stops and asks
rather than guessing. *Why:* this is the reflection pattern from the
architecture doc's §03 made literal — most naive agent builds route
blind on a bad classification; this one has an explicit, testable gate
against it.

**5. Row 2 — `persona_agent -> get_customer_segment`.** *Why
`invoke_tool()` and not a plain function call:* every tool call —
including PERSONA calling its own tool — crosses the same chokepoint,
so the policy check and this audit row fire unconditionally. No agent
can forget to log or skip a policy check, because there's no path that
doesn't go through it. This is the literal Agent Runtime from Fig.
01/03, not a diagram simplification.

**6. Inside that call, before it's logged (not its own row —
it's what makes row 2 possible):** `policy.check_tool_access()` —
default-deny, checks `caller` against `get_customer_segment`'s
`allowed_callers` list. *Why default-deny:* a newly added agent has
zero access to anything until explicitly allow-listed, rather than
being accidentally over-permissioned by default — try it live:
`POST /tools/get_customer_segment/invoke` with `"caller":
"concierge_agent"` comes back HTTP 403, not a silent no-op.

**7. Rows 3-8 — six `get_fit_profile` calls, not one.** This is the
evaluator loop (Fig. 03's dashed MUSE→TAILOR edge) actually happening:
MUSE calls TAILOR's tool once per distinct category among the
candidates, before ranking anything. *Why six and not ~500:* return-risk
depends only on `(customer, category)`, never the SKU — deduped after
load-testing at 1,300+ SKUs turned this into ~500 redundant calls per
query; the fix is a plain dict cache keyed by category, not new
infrastructure.

**8. Row 9 — `model_gateway:reasoning`.** The actual ranking call:
OpenRouter, then direct Anthropic, then the deterministic fallback, in
that order, each attempt's real failure logged rather than swallowed.
*Why three tiers:* a model call has more failure modes than almost
anything else in this system (network, billing, rate limits, malformed
JSON) — this build hit four distinct real ones getting a live key
working. None of them became a demo-blocking incident, because the
fallback was always there underneath.

**9. Row 10 — `rank_products`.** This is `invoke_tool()`'s *own* log
line for the outer call MUSE made — and it's last, not first, because
`invoke_tool()` logs after `contract.handler()` returns. Everything the
handler did internally (rows 3-9) necessarily logs before the call that
contains them finishes. *Why point this out specifically:* it's the
detail that proves the audit log isn't a curated summary — it's
recording real call order, including the parts that are non-obvious
until you've read the code.

**10. Back in `handle_message` (no new rows):**
`session_store.save()` writes `segment`/`last_results`/
`last_category_by_sku` — Redis if reachable, SQLite otherwise — and
`muse._log_search()` already wrote `behavioural` rows for what got
shown. *Why two separate memory tiers, not one:* session state has a
30-minute TTL by design and must never be where durable personalization
lives, or a Redis restart would erase a customer's actual history, not
just their current conversation. `preference_tags` (PERSONA) and
`recently_engaged` (MUSE) both read the durable copy on the next call,
regardless of what happens to this session.

**Close the loop:** run the exact same request again in a **new**
`session_id` (no shared state except SQLite) and show `intent_live_model`
/ `used_live_model` / the resulting segment are identical — durable
memory working, session memory irrelevant to it, proven by two audit
trails that look the same past row 1.

---

## Testing and explaining return-risk

Comes up naturally once someone notices several products in a grid show
the same "Return risk 35%" badge and asks whether it's real or a
placeholder. It's real — here's how to prove it live, and how to talk
about it.

**What it is, in one line:** TAILOR's estimate of how likely *this
customer* is to return an item in *this category* — not per-product
(there's no per-SKU return history to draw on), always per (customer,
category), which is also why every item from the same category in a
grid legitimately shows the identical number.

**Two sources, and the test that tells them apart:**
```bash
# same category, two customers — one has fit history for it, one doesn't
curl -s -X POST http://localhost:8000/tools/get_fit_profile/invoke -H "Content-Type: application/json" \
  -d '{"caller":"tailor_agent","args":{"customer_id":"CUST-PRIYA","category":"jeans"}}'
curl -s -X POST http://localhost:8000/tools/get_fit_profile/invoke -H "Content-Type: application/json" \
  -d '{"caller":"tailor_agent","args":{"customer_id":"CUST-JORDAN","category":"jeans"}}'
```
**Expect:** Priya (has a seeded jeans return) → `"return_risk": 0.12` —
TAILOR's fixed guided rate. Jordan (no history in that category) →
`"return_risk": 0.3929` — the **population baseline** for jeans
specifically, not a generic constant.

**Prove the baseline isn't asserted — it's a live query.** Run the exact
SQL TAILOR's `_population_baseline_return_risk()` runs, by hand, and
show it matches the API's number to four decimal places:
```bash
sqlite3 data/neutail.db "
SELECT
  (SELECT COUNT(*) FROM returns WHERE sku IN (SELECT sku FROM catalogue WHERE category='jeans')) AS returns,
  (SELECT COUNT(*) FROM transactional WHERE kind='order' AND sku IN (SELECT sku FROM catalogue WHERE category='jeans')) AS orders
"
```
**Expect:** `11|28` — and `11/28 = 0.3929`, exactly Jordan's number above.
No hidden fudge factor between the SQL and what the API returned.

**Show it varies genuinely across categories**, not just jeans vs.
jeans — different categories have different real return rates in the
seeded population, which is why a shoes badge can look very different
from a dress badge in the same grid:
```bash
for cat in dress jacket top shoes jeans; do
  curl -s -X POST http://localhost:8000/tools/get_fit_profile/invoke -H "Content-Type: application/json" \
    -d "{\"caller\":\"tailor_agent\",\"args\":{\"customer_id\":\"CUST-JORDAN\",\"category\":\"$cat\"}}" \
    | python3 -c "import json,sys; print('$cat:', json.load(sys.stdin)['return_risk'])"
done
```
**Expect** (numbers will drift slightly if reseeded, direction won't):
dress ~35%, jacket ~35%, top ~37%, shoes noticeably higher (~48%), jeans
~39% — several coincidentally round to the same displayed whole percent
even though the underlying values differ; shoes makes the point
unambiguous regardless.

**The talking point that ties it together:** the gap between 12%
(guided) and ~35-48% (baseline) *is* UC3's value proposition — TAILOR's
whole job is moving a customer from the baseline number to the guided
one by learning their fit pattern from a return. Zoom out to the same
effect at population scale in the **Admin tab**: baseline **42.8%**,
guided **8.2%**, computed from the same seeded data, not the same "34%
→10-15%" figure asserted in the original architecture doc — this is
that claim, actually measured.

---

## If something breaks mid-demo

| Symptom | Do this |
|---|---|
| Nothing happens after a search | Wait — a live call takes 15-40s and shows a spinner. If it's been >60s, something's actually stuck; check the next row. |
| `used_live_model` is `false` unexpectedly | Not a crash — the deterministic fallback kicked in. Keep going, the demo still works; check the Admin tab's audit trail afterward for the real reason if you want to know why (rate limit, truncated response, etc. are all logged there now, not swallowed). |
| "Can't reach the Agent Mesh Service" in Streamlit | `uvicorn` isn't running or died. `lsof -ti:8000 \| xargs kill; uvicorn neutail.api:app --port 8000 &` |
| Streamlit tab is blank/stale | It auto-reloads on file changes but not on API restarts — hit browser refresh. |
| Clicking "View details" does nothing / product page shows an error | The SKU comes from the URL (`?sku=...`) — if you edited or pasted a URL by hand, check the SKU is spelled exactly right. From a normal click-through this shouldn't happen; if it does on a genuine click, that's worth reporting, not just working around. |
| Escalation queue empty when you expected a row | Check you actually called `commit_subscription` with `amount` over $75 via the API — neither the search bar nor the "Talk to a stylist" button can request more than the standard $60 offer (see step 5). |
| Redis badge shows "sqlite" | Fine — it's the documented fallback, not a failure. Skip the Redis-specific talking point or restart `redis-server` before `uvicorn`. |

---

## After the demo

Nothing required — but if you're about to demo again later the same
day, reseed first (`rm -f data/neutail.db && python3 -m neutail.seed`)
so escalations/subscriptions from this run don't carry into the next
one and confuse the Admin tab's counts.
