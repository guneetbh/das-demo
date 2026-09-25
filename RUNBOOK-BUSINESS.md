# Neu.Tail — Business Outcomes Runbook

Every number below is computed live from the seeded population by
`GET /admin/outcomes` (`neutail/admin.py` — plain read-only SQL over
the tables every agent already reads, not a hardcoded slide). Reproduce
any of it yourself:

```bash
curl -s http://localhost:8000/admin/outcomes | python3 -m json.tool
```

Or in the UI: **📊 Admin tab → Business Outcomes**.

> Numbers here were captured 2026-09-25 against the seeded population
> plus this session's own testing (a few extra orders/escalations from
> demo walkthroughs). They'll drift slightly run to run — that's the
> point, they're computed, not asserted. For a clean pre-demo snapshot,
> reseed first: `rm -f data/neutail.db && python3 -m neutail.seed`.

---

## Headline: return rate, baseline vs. guided

| | Orders | Returns | Return rate |
|---|---|---|---|
| **Population blended** (everyone, all history) | 377 | 138 | **36.6%** |
| **Baseline** (no fit guidance yet for that category) | 315 | 133 | **42.2%** |
| **Guided** (TAILOR had already given fit guidance) | 62 | 5 | **8.1%** |

The pitch: **42% → 8%** once TAILOR's fit guidance exists for a
customer+category — comfortably inside, in fact ahead of, the 10-15%
target range. The 36.6% blended figure is the honest "what a retailer
sees today" number (seed.py's population-wide design target was 34%;
36.6% is what the causal simulation actually produced — expected RNG
variance, not a discrepancy to explain away).

This is a **real temporal split**, not two labels applied after the
fact: `fit_profile.created_at` records the moment guidance was
established for that customer+category, and an order only counts as
"guided" if it happened *after* that timestamp. A customer's own
earlier, pre-guidance orders correctly stay in the baseline bucket even
once they have a `fit_profile` row today. (Two earlier, wrong ways of
computing this — and why they were wrong — are in `README.md` under
"Admin — business outcomes"; worth reading before presenting this
number so a "how did you compute that" question doesn't land cold.)

**Why baseline (42.2%) reads higher than blended (36.6%):** the guided
cohort's low returns (8.1%) get pulled *out* of the pool once guidance
exists, so what's left behind skews a bit higher. That's the mechanism
working as intended, not an artifact — it's the same 315+62=377 orders
and 133+5=138 returns either way (`138/377 = 36.6%` blended, split into
the two rows above).

**Verify live:**
```bash
curl -s http://localhost:8000/admin/outcomes | python3 -c "
import json, sys
d = json.load(sys.stdin)['return_rate']
print(d['baseline_orders'], d['baseline_returns'], d['baseline_return_rate'])
print(d['guided_orders'], d['guided_returns'], d['guided_return_rate'])
"
```

---

## Conversion — directional, not a population-scale claim

| | Value |
|---|---|
| Searched (customer, category) pairs | 6 |
| Converted to >=1 order | 4 |
| **Search-to-purchase rate** | **66.7%** |

Be upfront about this one in a demo: N=6 is small — it's driven by the
hand-authored hero customers' behavioural events plus live testing
today, not a bulk-generated signal across all 40 seeded customers (the
bulk generator only populates `transactional`/`returns`/`fit_profile`
at scale, not `behavioural` — see `neutail/seed.py`). It's a real,
computed number and it demonstrates the *mechanism* (search → recommend
→ purchase is a traceable path through the schema), but don't present
66.7% as a validated population-level conversion lift. If asked for a
bigger-N version, that means extending the seed generator to also
synthesize bulk `behavioural` rows — not done yet, worth flagging as a
next step rather than glossing over.

---

## Governance loop — human-in-the-loop is actually exercised

| | Count |
|---|---|
| Subscriptions committed | 1 |
| Escalations raised (order + subscription) | 3 |
| — approved | 3 |
| — denied | 0 |
| — pending | 0 |

Small numbers, on purpose — the story here isn't a rate, it's that
SENTRY's policy thresholds (`AMOUNT_THRESHOLDS`, first-time-payer check)
actually pause a real commit and a human reviewer actually completes or
rejects it, end to end, with `audit_log` covering every step. See
`RUNBOOK-AGENTS.md` (SENTRY/CONCIERGE sections) for the mechanism and
`RUNBOOK.md` for a live approve-and-complete walkthrough.

---

## Population context

| | |
|---|---|
| Customers | 40 |
| SKUs | 1,300 |
| Orders | 377 |
| Loyalty points issued | 53,546 |
| Audit log rows | 193 |

Scale target from the original spec (30-50 customers, 1000+ SKUs, 12
months of transactions) — met, not just aimed at.

---

**In one line:** the number worth leading with is **42% → 8%** return
rate once fit guidance exists, because it's the one with real
population-scale N (377 orders) behind it and a temporal split that
rules out the "labeled after the fact" objection. Conversion and
subscription numbers are real but small-N — present them as proof the
mechanism works end to end, not as validated lift.
