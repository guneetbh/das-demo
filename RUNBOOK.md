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
Open http://localhost:8501 and confirm the Demo Chat tab loads with the
Priya/Jordan selector and a loyalty panel in the sidebar.

**5. Have a second window ready** on `http://localhost:8000/docs`
(Swagger UI) — one step below needs it, since the chat UI alone can't
trigger it (see step 5).

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

In the chat tab (either customer), type something that's deliberately
unclear:
> `hi`

**Expect:** *"Could you say a bit more? I want to route this to the
right specialist (best guess: unknown)."* — this is the self-check
firing, not a bug: the Orchestrator won't commit to a specialist below
a confidence threshold. (On fallback, "unknown" is always low
confidence by construction; live, the model can be *highly* confident
something's unclear — either way, `unknown` always clarifies.)

Then type a real request:
> `show me something for date night`

**Expect:** a clean discovery response, not a clarify — routing worked.

### 2 — Profiling *(PERSONA, UC1)*

Point at the response's segment line: `segment: affluent` for Priya,
`segment: value` for Jordan — resolved live from CRM + loyalty tier,
not stored or hand-typed. Switch customers in the sidebar and re-run
the same query to show it side by side.

### 3 — Discovery *(MUSE, UC2 — evaluator loop + diversity cap)*

Same query as above (`show me something for date night`) for **Priya**
specifically. In the results, point out:
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

**First, look at what step 3 actually returned** — the follow-up
resolves to whichever *position* you reference, and MUSE doesn't
guarantee jeans lands in Priya's top 4 (diversity cap + live-model
judgment mean the mix varies; confirmed missing entirely in more than
one validation run). Two ways to run this, in order of reliability:

**A — guaranteed, via the API** (use this if you need Priya's guided-fit
story to land every time, e.g. presenting to an audience with no room
for "let me try that again"):
```bash
curl -s -X POST http://localhost:8000/tools/get_fit_profile/invoke \
  -H "Content-Type: application/json" \
  -d '{"caller":"tailor_agent","args":{"customer_id":"CUST-PRIYA","category":"jeans"}}'
```
**Expect:** `"has_history": true, "guidance": "size up from M — past returns show this category runs small", "return_risk": 0.12`. Bypasses MUSE's ranking entirely — same tool TAILOR always uses, just not gated on whether jeans happened to rank.

**B — in the chat, if a jeans item did land in Priya's results** (check
its position in step 3's output first, then reference it by that exact
position — the resolver now understands `"second"`/`"third"`/`"fourth"`
and higher, plus `"4th"`/`"#4"` forms, not just second/third):
> `the fourth one, in my size` *(or whichever position it actually landed at)*

Either way, then switch to **Jordan**, run the same discovery query,
and ask any fit question — expect the **graceful no-history fallback**
("no fit history yet — using the standard size guide") regardless of
which item you reference, since she has no fit_profile row at all. Same
code path as Priya's guided answer; the seeded data is the only
difference.

### 5 — Upsell, including human-in-the-loop *(CONCIERGE/CARE/SENTRY, UC4)*

**In the chat**, as Priya:
> `I have a styling question`

**Expect:** an offer, approved inside policy ($60, well under the
$75/first-time-payer thresholds), a committed subscription, and points
earned in the sidebar loyalty panel (`+90 pts` for Priya's Gold 1.5×
multiplier).

**The chat always uses a fixed $60 offer, so it can't trip an
escalation on its own.** To show the human-in-the-loop path, switch to
the Swagger UI (`localhost:8000/docs`) → `POST
/tools/commit_subscription/invoke` → Try it out:
```json
{"caller": "lead_orchestrator", "args": {"customer_id": "CUST-PRIYA", "amount": 90.0, "plan": "premium"}}
```
**Expect:** `"committed": false, "escalated": true` — SENTRY didn't deny
it, it routed to a human. Go to the **Human Review Queue tab** in
Streamlit, find the pending row, and click **Approve** — the commit
completes and points post *at that moment*, not when it was first
requested. This is the point to make explicitly: the escalation isn't
a rejection, it's a pause.

### Bonus beats, if time allows

- **Cross-session memory** — click "New session" in the sidebar
  (simulates a return visit with no shared state) and re-run Priya's
  discovery query. Segment and fit signal are already there —
  recomputed live from durable tables, not carried over from the old
  session, which is what makes it survive a Redis restart too.
- **TALLY / loyalty** — point at the sidebar panel before and after the
  step 5 commit; the balance moves by exactly `amount × tier
  multiplier`, visible in real time.
- **The Admin tab** — the business-outcome numbers, computed from the
  seeded population, not asserted: baseline return rate **42.8%**
  drops to **8.2%** once fit guidance exists for that category — a
  real, queryable effect (`GET /admin/outcomes`), not the "34%→10-15%"
  figure quoted as a constant. Mention the one honest gap while you're
  there: search-to-purchase currently reads 0% on the freshly seeded
  data (no purchases yet correlate with searches until the demo itself
  generates some) — say that plainly rather than let a flat zero look
  like something's broken.
- **The audit trail** — `GET /audit?limit=20` or the chat's own
  expander. Every step above is a row here, policy-checked. This is
  the answer to "how do we know this is actually enforcing anything" —
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

## If something breaks mid-demo

| Symptom | Do this |
|---|---|
| Chat shows nothing after sending a message | Wait — a live call takes 15-40s and (as of the latest fix) shows a spinner. If it's been >60s, something's actually stuck; check the next row. |
| `used_live_model` is `false` unexpectedly | Not a crash — the deterministic fallback kicked in. Keep going, the demo still works; check `GET /audit?limit=10` afterward for the real reason if you want to know why (rate limit, truncated response, etc. are all logged there now, not swallowed). |
| "Can't reach the Agent Mesh Service" in Streamlit | `uvicorn` isn't running or died. `lsof -ti:8000 \| xargs kill; uvicorn neutail.api:app --port 8000 &` |
| Streamlit tab is blank/stale | It auto-reloads on file changes but not on API restarts — hit browser refresh. |
| Escalation queue empty when you expected a row | Check you actually called `commit_subscription` with `amount` over $75 — the chat's own $60 offer never trips it (see step 5). |
| Redis badge shows "sqlite" | Fine — it's the documented fallback, not a failure. Skip the Redis-specific talking point or restart `redis-server` before `uvicorn`. |

---

## After the demo

Nothing required — but if you're about to demo again later the same
day, reseed first (`rm -f data/neutail.db && python3 -m neutail.seed`)
so escalations/subscriptions from this run don't carry into the next
one and confuse the Admin tab's counts.
