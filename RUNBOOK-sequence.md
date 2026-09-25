# Neu.Tail — Blueprint-to-Build Sequence Mapping

Maps every flow demonstrated across `RUNBOOK.md`, `RUNBOOK-AGENTS.md`,
`RUNBOOK-MEMORY.md`, and `RUNBOOK-BUSINESS.md` back to **Fig. 02 —
End-to-end sequence, Priya's session** from the Mission #3 blueprint
(the Neu.Tail architecture artifact, §06 "The Demo"), and states where
the build deviated from that diagram and why. Nothing here is
re-verified from scratch — every claim below cites the runbook section
that already carries the live evidence.

Fig. 02 itself carries a second layer of citation worth knowing about:
its own caption maps each of its 8 steps back to **Mission #3's own
three sequence diagrams** (Fig. 1/3, 2/3, 3/3) — the earlier, larger
blueprint this one was cut down from. Those references are reproduced
below as "Mission #3 ref" for completeness; they aren't independently
checked here, since Mission #3's diagrams aren't part of this repo.

---

## Quick-reference table

| Fig. 02 step | Mission #3 ref | Built as | Demonstrated in | Deviation? |
|---|---|---|---|---|
| ① Intent + confidence check | Fig. 1/3, steps 1–5 | `orchestrator.classify_intent()` | RUNBOOK.md §1 | Reflection made *live*, not heuristic — see below |
| ② PERSONA / segment | Fig. 1/3, steps 5–8 | `persona.run()` | RUNBOOK.md §2 | Segment recomputed every call, never stored |
| ③ MUSE / discovery + evaluator loop | Fig. 1/3, steps 9–14 | `muse.run()` → `tailor.get_fit_profile` per category | RUNBOOK.md §3, RUNBOOK-AGENTS.md §6 | Diversity cap + prompt prefilter added, not in blueprint |
| ④ Session memory | *(not cited)* | `session_store.py` (Redis + SQLite fallback) | RUNBOOK-MEMORY.md §1 | **Redis wasn't in the blueprint at all** |
| ⑤ TAILOR / fit | Fig. 3/3 | `tailor.run()` | RUNBOOK.md §4 | Baseline risk computed live, not a static number |
| ⑥ Upsell + human-in-the-loop | Fig. 2/3 | `concierge.py`, `sentry.py`, `care.py`, `human_review.py` | RUNBOOK.md §5, RUNBOOK-AGENTS.md §7 | **`commit_order` added — not in Fig. 03's component model** |
| ⑦ Cross-session memory | *(not cited)* | Durable SQLite tables, read live every call | RUNBOOK-MEMORY.md §2–3 | Tier doesn't auto-recompute from new points (disclosed gap) |
| ⑧ Audit trail / business outcome | *(not cited)* | `audit_log` table, `neutail/admin.py` | RUNBOOK.md architecture walkthrough, RUNBOOK-BUSINESS.md | Numbers actually computed (42%→8%), not just asserted as a target |

Two flows we've since demonstrated have **no Fig. 02 step at all** —
covered in "Beyond the diagram" below.

---

## ① Orchestration & intent — reflection

**Fig. 02:** Client → Orchestrator, a self-loop box labeled "confidence
check — low-confidence intent asks, doesn't route blind (reflection)",
then routes to PERSONA. Mission #3 ref: Fig. 1/3, steps 1–5.

**Built:** `orchestrator.classify_intent()` — RUNBOOK.md §1, live-verified
(`intent_live_model: true` in the `/chat` response), with the keyword
matcher as `gateway.call_model`'s deterministic fallback.

**Deviation:** the blueprint's self-loop is drawn as an internal
Orchestrator check — it doesn't show it crossing the Model Gateway the
way step② and step③ explicitly do. The build makes it a **real live
model call** (fast tier), which is a stronger, more literal
implementation than the diagram requires, not a weaker one — but it
does mean this step now has a Model Gateway hop Fig. 02 doesn't draw
(Fig. 01's general architecture licenses this — "every model call
crosses the Gateway" — Fig. 02 just doesn't spell it out at this
specific step).

A second, unplanned addition: `_resolve_ordinal_reference()` grew a
`has_recent_results` flag and extended ordinal-word/digit matching
(RUNBOOK.md §1, RUNBOOK-MEMORY.md §1) after live testing showed the
original "session memory" note in step④ was under-specified — the
blueprint says a follow-up needs "no restated context" but doesn't say
*how* it's resolved. Built because testing surfaced it, not because
the diagram asked for it.

## ② PERSONA — segmentation

**Fig. 02:** Orchestrator → PERSONA → Data Universe → back, "affluent /
value". Mission #3 ref: Fig. 1/3, steps 5–8.

**Built:** `persona.run()` → `get_customer_segment`, matches exactly —
RUNBOOK.md §2, RUNBOOK-AGENTS.md §2.

**Deviation:** §07 of the blueprint lists "Resolved segment —
affluent / value" under **Long-term (durable)** memory, implying it's
stored. The build never stores it — `README.md`: "segment is never
stored," recomputed live from CRM/loyalty/behavioural on every call.
Favorable in effect (never stale) but not literally what §07 depicts.

## ③ MUSE — discovery + evaluator loop

**Fig. 02:** Orchestrator → MUSE → Data Universe, then a self-loop
"re-rank vs. TAILOR's cached return-risk before the feed ships
(evaluator loop)", then back to Orchestrator → Client. Mission #3 ref:
Fig. 1/3, steps 9–14.

**Built:** `muse.run()` — RUNBOOK.md §3, RUNBOOK-AGENTS.md §6, and the
default-recs path in RUNBOOK-AGENTS.md §6b. The evaluator loop is real:
`get_fit_profile` called once per distinct candidate *category* (7
calls in the §6b trace), not the single abstracted arrow Fig. 02 draws.

**Deviations, all additions not present in the blueprint:**
- **Diversity cap** (`_apply_diversity_cap`, max 2 per category) — added
  after seeding to real scale (1,300 SKUs) showed one category filling
  every slot, a failure mode invisible at the blueprint's small demo
  scale. `README.md` "Category diversity in MUSE's results."
- **Prompt prefiltering** (60 candidates, 12/category cap before the
  live-model prompt is even built) — a token-budget necessity the
  diagram doesn't need to address at diagram-level abstraction.
- **Cross-session durability boost** — `README.md` "Recommendations
  that survive the session": a search today nudges ranking on a
  *different* session tomorrow, via `behavioural`. Not in §07's memory
  model at all, which only covers within-session and across-session
  for the *same* customer's own segment/fit/tier, not ranking feedback
  from recent activity.

## ④ Session memory

**Fig. 02:** a note box — "the second one, in my size — no restate
needed." Not tied to a Mission #3 reference in the caption.

**Built:** `session_store.py` — RUNBOOK-MEMORY.md §1, live-verified via
`redis-cli get` and a fresh-session negative-control test (§3).

**Deviation — the most significant infrastructure gap between blueprint
and build:** §07's own prose is explicit — "Both tiers live on the same
SQLite file — a `session_context` table for short-term..." — no other
store is mentioned anywhere in the blueprint, and §08's tech stack table
has no separate row for session memory at all (it's folded into
"Embedded store"). The build uses **Redis, with SQLite as the fallback**, not
the reverse. This was a mid-conversation build decision (the user asked
whether Redis was in use and, on confirming it wasn't, asked to add
it), not something the original design called for or anticipated. The
fallback shape (`gateway`-style live/fallback) keeps the blueprint's
SQLite-only path fully working when Redis isn't reachable, so nothing
in §07 broke — it's additive, but it is a real deviation from what §08
names as *the* mechanism, not *a* mechanism.

## ⑤ TAILOR — fit & size

**Fig. 02:** Orchestrator → TAILOR → Data Universe → back, "size-up ·
return-risk ↓". Mission #3 ref: Fig. 3/3.

**Built:** `tailor.run()` — RUNBOOK.md §4, RUNBOOK-AGENTS.md §3, exact
match: guided guidance for Priya's jeans, graceful no-history fallback
for Jordan, as the blueprint's two customer profiles (§06 "Customer A /
B") specify.

**Deviation:** minor, favorable — `_population_baseline_return_risk()`
computes the baseline live from seeded orders/returns per category,
where the diagram abstracts this as just "return-risk score" with no
stated method. `README.md`'s TAILOR section documents this as a
refinement, not a gap.

## ⑥ Service upsell — CARE, SENTRY, CONCIERGE, human-in-the-loop

**Fig. 02:** Client → CONCIERGE ("styling question"), a note box for
CARE+SENTRY gating CONCIERGE's calls, a human-in-the-loop note
("outside policy bounds → Human Review Queue"), then
`commit_subscription` → Data Universe → confirmation. Mission #3 ref:
Fig. 2/3.

**Built:** `concierge.py` + `sentry.py` + `care.py` + `human_review.py`
— RUNBOOK.md §5 (both the inside-policy and escalated paths, live),
RUNBOOK-AGENTS.md §7 (the full escalation-to-completion loop, including
the bug found and fixed there).

**Deviation — the largest scope addition to any single step:**
`commit_order` (the "Buy now" flow, its own SENTRY threshold, its own
escalation path) does not exist in the blueprint. Fig. 03's component
model draws CONCIERGE providing exactly one interface,
`«commit_subscription»`, and §04's agent table lists only that same key
call. `commit_order` was added mid-build (the user asked for a buy
button so loyalty points would visibly accrue on a purchase, not just a
subscription) and required a real schema change — `escalations.sku`,
absent from the original design's `escalations` table entirely — to
carry a product-purchase escalation the same way a subscription
escalation is carried. This is also exactly how a real bug reached a
user: `human_review.resolve_escalation()` was written against the
one-`kind` blueprint shape and didn't know how to complete an `order`
escalation until that gap was found and fixed (RUNBOOK-AGENTS.md §7,
the correction note at the top of that document).

## ⑦ Cross-session memory

**Fig. 02:** a closing note — "next day, same customer — segment / fit
/ tier loaded from the store, not re-derived." Not tied to a Mission #3
reference.

**Built:** durable SQLite tables (`customers`, `loyalty`, `fit_profile`)
read live on every call — RUNBOOK-MEMORY.md §2, verified with a
brand-new `session_id` for the same customer.

**Deviation:** the note's own wording — "new subscription tier loaded
from the store" — implies a subscription changes tier. It doesn't, on
purpose: `README.md`'s TALLY section and RUNBOOK-AGENTS.md's "Honest
gaps" both disclose that **tier is never recomputed from
`points_balance`**, specifically to avoid a silent feedback loop into
PERSONA's segmentation logic. Points move; tier (and therefore segment
eligibility) doesn't move with them in this build. A real, intentional
divergence from what step⑦'s own caption describes.

## ⑧ Audit trail & business outcome

**Fig. 02:** a closing note — "every arrow (and self-check) above
crossed the Gateway or the Agent Runtime, policy-checked — this trace
is the log." §09's stat: **34% → 10–15%** return rate as the headline
target KPI, asserted, no computation shown.

**Built:** `audit_log` — RUNBOOK.md's architecture walkthrough section
quotes a real trace; `neutail/admin.py` / `GET /admin/outcomes` —
RUNBOOK-BUSINESS.md.

**Deviation:** favorable — the blueprint states 34%→10–15% as a target,
not a computed result. The build actually computed it, and getting a
*real* effect out of the seed data took two failed methodology attempts
before landing on a genuine temporal split (`README.md` "Admin —
business outcomes," RUNBOOK-BUSINESS.md's headline section). Live
result: **42.2% baseline → 8.1% guided**, beating the target range, on
N=377 real orders — not something Fig. 02/§09 asked for at this level
of rigor, but a strictly stronger version of the same claim.

---

## Beyond the diagram — flows with no Fig. 02 equivalent

**TALLY (loyalty agent) — built despite being explicitly scoped out.**
§01 "Build scope" and §09 "Outcomes" both state, in so many words, that
UC5/TALLY was *not* selected for the build ("UC4 Upsell selected...
because it already has a Mission #3 sequence diagram to build against").
The actual build implements `agents/tally.py` in full —
`get_loyalty_status`, `earn_points`, tier multipliers — wired into both
`commit_subscription` and `commit_order`, and exercised in nearly every
runbook (RUNBOOK.md §5, RUNBOOK-AGENTS.md §7, RUNBOOK-MEMORY.md, the
"Buy now" flow). This is the single largest deviation in the whole
build: an entire use case the blueprint deliberately left out. Reason:
the user asked for visible points accrual on both subscription and
purchase flows partway through the build, and TALLY was the natural
place to put it — there was no sequence diagram to build against, so it
was built directly against the tool-contract pattern the other agents
already established (§03/§05), not against a diagram.

**Default recommendations (Streamlit auto-load) — a UI-only elaboration
of step③, not a new step.** `streamlit_app.py`'s `load_recommended()`
fires the identical `/chat` → Orchestrator → PERSONA → MUSE → TAILOR
path as a typed discovery search, automatically, on first page view.
README.md's "Default recommendations fire the real pipeline" section
and RUNBOOK-AGENTS.md §6b both document this. It has no corresponding
box or note anywhere in Fig. 02 — the blueprint's demo script assumes a
customer always types something first. Not a deviation from any drawn
step, since it reuses step③'s wire path exactly; more a UX decision the
diagram never had an opinion on either way.

**Deployment — 4 containers collapsed to 2 processes, OPA/Rego dropped
for embedded Python.** Fig. 04 draws four separate containers (Streamlit
:8501, Agent Mesh Service :8000, **Model Gateway :8010** as its own
FastAPI-proxy-over-LiteLLM process, **Policy Engine :8181** running OPA
with Rego rules per §05's code sample) plus an external Model Provider
API. The actual build runs **two** processes — `streamlit run` and
`uvicorn neutail.api:app` — with `neutail/gateway.py` and
`neutail/policy.py` as plain importable modules inside the single
FastAPI app, no OPA, no LiteLLM, no ports 8010/8181 anywhere in the
codebase. Worth stating precisely: **this deviation was pre-authorized
by the blueprint itself** — §08's own tech-stack table lists "OPA
(Rego), **or an embedded YAML rule evaluator if time-boxed**" for the
policy engine. `policy.py`'s own docstring says as much: "An embedded
rule evaluator rather than OPA/Rego, per §08's time-boxed fallback —
same default-deny shape as the Rego example in the doc." The Model
Gateway's collapse into an in-process module follows the same
time-boxing logic, just not named as an explicit option the way the
policy engine was.

**Streamlit client — a storefront, not "a small chat UI."** §08 specs
the demo client as "CLI or a small Streamlit chat UI." The build ended
up as a full storefront — search bar, product grid, detail pages, a Buy
button, three tabs (Shop / Human Review / Admin) — after the user asked
midway through the build to reshape it into something that reads as a
retail site rather than a chat transcript. Functionally it's still the
same `/chat` call underneath (confirmed in this document's own step①–③
mappings), so no wire-level deviation — but it's a substantially larger
UI than §08 scoped.

---

## What this means, in one line

Every one of Fig. 02's 8 steps has a real, live-verified counterpart in
this build — nothing in the diagram was skipped. The deviations cluster
in three honest categories: **additions the blueprint explicitly
deferred** (TALLY, `commit_order`), **infrastructure swaps the blueprint
either didn't anticipate** (Redis) **or explicitly pre-authorized**
(embedded policy engine instead of OPA), and **rigor upgrades** on
things the diagram only asserted (live reflection instead of a bare
heuristic, a computed 42%→8% instead of a stated 34%→10–15% target).
None of them make a demonstrated flow diverge from what Fig. 02 promises
a viewer would see — they make several of those flows do more than the
diagram required.
