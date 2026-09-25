# Neu.Tail — Agents Implementation & Verification Runbook

**Status: implemented and verified end to end**, as of commit `1d20b2b`
on a freshly seeded database (`rm -f data/neutail.db && python3 -m
neutail.seed`). Every result below was captured live through the
running API in one pass while writing this document — not carried
forward from earlier testing, not asserted. Reproduce any line of it
with the `curl` command shown; nothing here needs the UI to verify.

**One correction since the first version of this document** (commit
`645c681`): the original CONCIERGE section verified `commit_order`
only up through escalation *creation* — it never tested the full
approve-and-complete loop. That gap in this document's own coverage is
exactly how a real bug reached a user: `resolve_escalation()` only knew
how to complete a `subscription` escalation, so approving an `order`
one silently did nothing (status flipped to "approved," no purchase, no
points). Fixed in `1d20b2b`; the CONCIERGE section below now includes
the test that was missing.

This is a different document from `RUNBOOK.md` (how to run a live
five-functionality demo) and `README.md` (how the system works,
developer reference) — this one answers one question: **is the
Orchestrator, and every specialized agent it routes to, actually
implemented and provably working, right now, on this commit?**

---

## What "the orchestrator and specialized agents" means here

One Orchestrator (`neutail/orchestrator.py`), seven specialized agents
(`neutail/agents/*.py`), all reachable only through the Agent Runtime's
`invoke_tool()` chokepoint (`neutail/runtime.py`) — policy-checked
(`neutail/policy.py`, default-deny) and audit-logged
(`audit_log` table) on every single call, no exceptions, including an
agent calling its own tool.

| # | Agent | Provides | UC |
|---|---|---|---|
| — | **Lead Orchestrator** | routes every message; confidence-check (reflection) before committing to a specialist | §02/§03 |
| 1 | **PERSONA** | `get_customer_segment` | UC1 |
| 2 | **TAILOR** | `get_fit_profile` | UC3 |
| 3 | **CARE** | `resolve_contact` | UC4 |
| 4 | **SENTRY** | `check_payment_policy` | UC4 |
| 5 | **MUSE** | `rank_products` | UC2 |
| 6 | **CONCIERGE** | `commit_subscription`, `commit_order` | UC4 |
| 7 | **TALLY** | `get_loyalty_status`, `earn_points` | UC5 |

---

## The policy chokepoint, proven before anything else

Every claim below rests on this: an agent literally cannot call a tool
it isn't allow-listed for. Not "doesn't," **can't**:

```bash
curl -s -w " [HTTP %{http_code}]" -X POST http://localhost:8000/tools/get_customer_segment/invoke \
  -H "Content-Type: application/json" \
  -d '{"caller":"concierge_agent","args":{"customer_id":"CUST-PRIYA"}}'
```
**Captured result:**
```
{"detail":"concierge_agent cannot call get_customer_segment: not in allowed_callers ['lead_orchestrator', 'persona_agent']"} [HTTP 403]
```

---

## 1 — Orchestrator: routing + reflection

```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"verify","customer_id":"CUST-PRIYA","message":"hi"}'
```
**Captured:** `{"type":"clarify","intent_guess":"unknown","confidence":0.95,...}`

Below `CONFIDENCE_THRESHOLD` (0.5), or intent is `"unknown"` at any
confidence, the Orchestrator returns a clarifying question instead of
routing blind — the reflection pattern from §03, not a diagram
fiction. Routing itself is a live fast-tier model call
(`classify_intent()`), with the original keyword matcher as its
deterministic fallback if the model is unreachable or its response
doesn't parse.

## 2 — PERSONA: `get_customer_segment`

```bash
curl -s -X POST http://localhost:8000/tools/get_customer_segment/invoke -H "Content-Type: application/json" \
  -d '{"caller":"persona_agent","args":{"customer_id":"CUST-PRIYA"}}'
```
**Captured:** `{"segment":"affluent","tenure_months":18,"preference_tags":["dress"]}` (Priya, Gold tier)
**Captured:** `{"segment":"value","tenure_months":2,"preference_tags":["dress"]}` (Jordan, Bronze tier)

Segment is derived live from `customers` + `loyalty` on every call —
never stored, never stale.

## 3 — TAILOR: `get_fit_profile`

```bash
curl -s -X POST http://localhost:8000/tools/get_fit_profile/invoke -H "Content-Type: application/json" \
  -d '{"caller":"tailor_agent","args":{"customer_id":"CUST-PRIYA","category":"jeans"}}'
```
**Captured (Priya, has jeans history):**
`{"has_history":true,"preferred_size":"M","runs":"small","guidance":"size up from M — past returns show this category runs small","return_risk":0.12,...}`

**Captured (Jordan, same category, no history):**
`{"has_history":false,"guidance":"no fit history yet — using the standard size guide","return_risk":0.3929}`

`0.3929` isn't a constant — it's `_population_baseline_return_risk()`
computing `returns / orders` for jeans specifically, live, from the
seeded population. (Full derivation and cross-checked SQL in
`RUNBOOK.md`'s "Testing and explaining return-risk" section.)

## 4 — CARE: `resolve_contact`

```bash
curl -s -X POST http://localhost:8000/tools/resolve_contact/invoke -H "Content-Type: application/json" \
  -d '{"caller":"care_agent","args":{"customer_id":"CUST-PRIYA"}}'
```
**Captured:** `{"customer_id":"CUST-PRIYA","contact_channel":"email","masked_contact":"p***a@example.com","upsell_flag":true}`
**Captured (Jordan):** `"masked_contact":"j****n@example.com","upsell_flag":false`

PII masked before it ever leaves the tool; upsell eligibility gated by
loyalty tier (Gold/Platinum only).

## 5 — SENTRY: `check_payment_policy` (all four real scenarios)

```bash
curl -s -X POST http://localhost:8000/tools/check_payment_policy/invoke -H "Content-Type: application/json" \
  -d '{"caller":"sentry_agent","args":{"customer_id":"CUST-PRIYA","amount":60.0,"kind":"subscription"}}'
```

| Scenario | Captured result |
|---|---|
| Priya, $60 subscription | `{"approved":true,"escalated":false}` |
| Priya, $90 subscription (over $75 bound) | `{"approved":false,"escalated":true,"escalation_id":1,"reason":"amount $90.00 over $75.00 policy bound for subscription"}` |
| Priya, $255 order (under $300 order bound) | `{"approved":true,"escalated":false}` |
| Jordan, $49 order (first-time payer) | `{"approved":false,"escalated":true,"escalation_id":2,"reason":"first-time payer"}` |

Orders and subscriptions carry different thresholds on purpose (§README
"Buying a product"); the first-time-payer check applies to both kinds
identically, which is what makes row 4 escalate on a modest amount.

## 6 — MUSE: `rank_products` (evaluator loop + diversity cap, via the Orchestrator)

```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"verify","customer_id":"CUST-PRIYA","message":"show me something for date night"}'
```
**Captured:** `used_live_model: true`, categories `[dress, dress, jacket, jacket]` — capped at 2 per category, confirmed no category exceeds the cap. Each item's `return_risk` came from a live `get_fit_profile` call per distinct category (6 such calls in the audit trail below for this one request, not 4 — one per candidate *category*, not per SKU).

## 6b — The Streamlit storefront's default recommendations use this same path

Since the last UI change, `streamlit_app.py` auto-loads a "Recommended
for you" grid the moment a fresh session starts (`load_recommended()`),
in the space that used to hold the logo. It's not a shortcut around the
agent mesh — it sends a synthetic prompt through the identical `/chat`
call used above, so it produces the identical call shape. Verified live
against a brand-new session (`audit_log`, ascending `log_id`):

```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"agentcall-verify","customer_id":"CUST-PRIYA","message":"recommend something for me today"}'
curl -s "http://localhost:8000/audit?limit=11"
```
**Captured:**
```
239 lead_orchestrator -> model_gateway:fast        (live intent classification)
240 persona_agent     -> get_customer_segment       (local SQL)
241-247 muse_agent     -> get_fit_profile ×7         (evaluator loop, one call per candidate category)
248 muse_agent         -> model_gateway:reasoning   (live MUSE ranking call)
249 muse_agent         -> rank_products              (tool-contract wrapper, logs the full result)
```
`type: "discovery"`, `used_live_model: true`, `intent_live_model: true`
— a real live-model round trip, same as any typed search, fired
automatically once per fresh session (and again on "New session"). The
tradeoff worth knowing: previously a live call only happened when a
customer typed a search; this adds 2 live calls (~15-25s) per session
start regardless of whether they ever search. Fine at pilot scale, worth
revisiting before real traffic (see README's "Default recommendations
fire the real pipeline" section for the fuller writeup). Same
live/fallback safety net applies — no new failure mode introduced.

## 7 — CONCIERGE: `commit_order` and `commit_subscription`, plus TALLY

```bash
curl -s http://localhost:8000/customers/CUST-PRIYA/loyalty   # before
curl -s -X POST http://localhost:8000/tools/commit_order/invoke -H "Content-Type: application/json" \
  -d '{"caller":"concierge_agent","args":{"customer_id":"CUST-PRIYA","sku":"P-DRS-006","quantity":1}}'
curl -s http://localhost:8000/customers/CUST-PRIYA/loyalty   # after
```
**Captured before:** `points_balance: 4200`
**Captured order result:** `{"sku":"P-DRS-006","amount":255.0,"committed":true,"points_earned":382,"points_balance":4582}`
**Captured after:** `points_balance: 4582` — matches exactly (`4200 + 255×1.5 = 4582`), confirmed against the loyalty endpoint independently of the order response, not just trusting the same call.

`commit_subscription` (the "Talk to a stylist" path) verified separately: `committed: true, amount: 60.0, plan: "standard", offer_text` 18 words (tightened per the latest change — price is now its own field, never parsed out of prose).

**The full escalation-to-completion loop** (the test that was missing before, per the correction at the top of this document):
```bash
curl -s -X POST http://localhost:8000/tools/commit_order/invoke -H "Content-Type: application/json" \
  -d '{"caller":"concierge_agent","args":{"customer_id":"CUST-JORDAN","sku":"V-ATH-108","quantity":1}}'
# -> escalated (first-time payer), escalation_id captured
curl -s -X POST http://localhost:8000/escalations/<id>/resolve -H "Content-Type: application/json" \
  -d '{"approve":true,"resolved_by":"streamlit_reviewer"}'
curl -s http://localhost:8000/customers/CUST-JORDAN/loyalty   # confirm independently
```
**Captured:** escalation → `{"order_id":"ORD-CUST-JORDAN-33f7959290","points_earned":22,"points_balance":164}` on approval; loyalty endpoint independently confirms `points_balance: 164`. The `escalations` table now carries the `sku` (added in `1d20b2b`) so `resolve_escalation()` has something to complete the order with — that column didn't exist when the first version of this document was written.

---

## The whole chain, in one audit trail

This is the literal sequence from three of the requests above, in
call order, nothing curated or reordered:

```
10 [OK] sentry_agent      -> check_payment_policy      ($90 subscription escalation test)
11 [OK] sentry_agent      -> check_payment_policy       ($255 order, Jordan first-time-payer test)
12 [OK] lead_orchestrator -> model_gateway:fast          (intent classification, discovery search)
13 [OK] persona_agent     -> get_customer_segment        (PERSONA)
14 [OK] muse_agent        -> get_fit_profile              } evaluator loop —
15 [OK] muse_agent        -> get_fit_profile              } one call per distinct
16 [OK] muse_agent        -> get_fit_profile              } category among the
17 [OK] muse_agent        -> get_fit_profile              } candidates, not per SKU
18 [OK] muse_agent        -> get_fit_profile              }
19 [OK] muse_agent        -> get_fit_profile              }
20 [OK] muse_agent        -> model_gateway:reasoning      (MUSE's live ranking call)
21 [OK] muse_agent        -> rank_products                 (logs last — invoke_tool logs after its handler returns)
22 [OK] tally_agent       -> get_loyalty_status            (sidebar loyalty read)
23 [OK] concierge_agent   -> check_payment_policy         } commit_order:
24 [OK] concierge_agent   -> earn_points                  } policy -> ledger -> points,
25 [OK] concierge_agent   -> commit_order                 } same chokepoints as everything else
26 [OK] tally_agent       -> get_loyalty_status            (loyalty re-read, confirms the balance moved)
27 [OK] lead_orchestrator -> model_gateway:fast            (intent classification, "I have a styling question")
28 [OK] care_agent        -> resolve_contact               (CARE)
29 [OK] concierge_agent   -> resolve_contact                } commit_subscription requires
30 [OK] concierge_agent   -> model_gateway:reasoning        } resolve_contact, then composes
31 [OK] concierge_agent   -> check_payment_policy           } the offer, then checks policy,
32 [OK] concierge_agent   -> earn_points                    } then commits and earns points
33 [OK] concierge_agent   -> commit_subscription             }
34 [OK] lead_orchestrator -> model_gateway:fast            (the "hi" clarify test, run last)
```

Every row `allowed`. Zero denials in this run because every caller used
was the correct one — the earlier `concierge_agent -> get_customer_segment`
403 was a deliberate wrong-caller test, captured separately above, and
does show up in the full log with `allowed: 0` if you pull more rows.

---

## Honest gaps — not silently omitted

- **No auth on `/tools/{name}/invoke`** — `caller` is self-declared by
  whoever calls it. The runtime's policy check is the real trust
  boundary for a local demo; this endpoint should not be exposed to an
  untrusted client as-is.
- **`search_to_purchase` in `/admin/outcomes` is a proxy**, not
  attribution — no impression-level link from a specific MUSE
  recommendation to a specific later purchase in the schema.
- **Tier isn't recomputed from `points_balance`** — a deliberate gap
  (see README's TALLY section) to avoid a silent feedback loop into
  PERSONA's segmentation.
- **The Streamlit UI hasn't been clicked through in a real browser**
  this session (no browser tooling available) — every API call the UI
  makes has been verified directly, as shown throughout this document,
  but the click-through experience itself is unverified by me.
- **MUSE's evaluator-loop demo beat (a specific category's discounted
  risk visibly winning a slot) isn't guaranteed live** — the model
  makes its own ranking judgment; confirmed absent in more than one
  run. The mechanism (the tool call happens, the risk value is correct
  when it does show up) is proven above regardless.

---

## Reproduce this yourself

```bash
cd /Users/guneetbhatia/WORK/das-demo
rm -f data/neutail.db && python3 -m neutail.seed
uvicorn neutail.api:app --port 8000 &
# then run any curl command from this document — every one is copy-pasteable as shown
```
