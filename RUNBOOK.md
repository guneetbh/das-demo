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

Follow up in the **same session**, no context restated:
> `the second one, in my size`

**Expect:** sizing guidance resolved from session memory (which item
"the second one" means) plus TAILOR's fit call. For **Priya**, if a
jeans item is in play: *"size up ... past returns show this category
runs small."* Switch to **Jordan**, run the same discovery query, then
ask a fit question — expect the **graceful no-history fallback**
("no fit history yet — using the standard size guide") instead of an
error. Both are the same code path; the only difference is the seeded
data.

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
