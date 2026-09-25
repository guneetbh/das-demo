# Neu.Tail — Memory & Context Runbook

Two tiers (§07), two different stores, verified separately below —
every result captured live against a fresh seed.

| | Session (short-term) | Durable (long-term) |
|---|---|---|
| **Holds** | `segment`, `last_results` (SKUs shown), `last_category_by_sku` | customer segment inputs, fit history, loyalty/points, behavioural log |
| **Store** | Redis (30-min TTL) → SQLite `session_context` if Redis is down | SQLite only — `customers`, `loyalty`, `fit_profile`, `behavioural`, `transactional` |
| **Scoped by** | `session_id` | `customer_id` |
| **Survives a new session?** | No — by design | Yes — that's the point |
| **Code** | `neutail/session_store.py` | every agent just reads its own table live |

---

## 1 — Within a session: a follow-up needs no context restated

```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"mem-demo-A","customer_id":"CUST-PRIYA","message":"show me something for date night"}'
```
**Results, in order:** `1. P-DRS-0106  2. P-DRS-0133  3. P-JKT-0108  4. P-JNS-0138`

```bash
redis-cli get "session:mem-demo-A:last_results"
```
**Captured:** `["P-DRS-0106", "P-DRS-0133", "P-JKT-0108", "P-JNS-0138"]` — TTL `1762`s (`redis-cli ttl ...`), i.e. it expires, on purpose.

```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"mem-demo-A","customer_id":"CUST-PRIYA","message":"the second one, in my size"}'
```
**Captured:** `"sku": "P-DRS-0133"` — index 1, exactly "the second one" from the list above. No product mentioned in the follow-up; resolved purely from what Redis is holding for this `session_id`.

**From the UI:** as Priya, type `show me something for date night` in
the search bar. Note the sidebar caption — `session: ui-CUST-PRIYA-xxxx
· memory: 🟥 redis`. In the *same* search bar (don't click "New
session"), type `the second one, in my size` — the fit-guidance banner
that comes back should match whichever product actually rendered
second in the grid, the same "no product named, resolved from session
state" behavior as the curl proof above. (Use the search bar for this,
not a product page's "Check my fit" button — that button reads the SKU
straight from the URL and never touches session memory at all, so it
doesn't demonstrate this specific mechanism.)

## 2 — Across sessions: durable data, recomputed live, not copied

Brand-new `session_id`, same customer, nothing shared with session A:
```bash
redis-cli keys "session:mem-demo-B:*"   # -> empty
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"mem-demo-B","customer_id":"CUST-PRIYA","message":"show me something for date night"}'
```
**Captured:** `"segment": "affluent"` — same answer as session A, but recomputed from `customers`+`loyalty`, not carried over (there was nothing to carry — the key above didn't exist).

```bash
curl -s -X POST http://localhost:8000/tools/get_fit_profile/invoke -H "Content-Type: application/json" \
  -d '{"caller":"tailor_agent","args":{"customer_id":"CUST-PRIYA","category":"jeans"}}'
```
**Captured:** `"has_history": true, "guidance": "size up from M — past returns show this category runs small"` — her jeans-return history from `fit_profile`, independent of which session asks.

**From the UI:** click **"New session (simulate 'next day')"** in the
sidebar. The `session_id` in the caption changes to a fresh UUID, and
the results grid resets to "Popular searches" — that reset *is* the
session-memory boundary, visibly. Search `show me something for date
night` again — same segment-driven results as before. If a jeans item
lands in the grid, click into it and hit **"📏 Check my fit"** — her
guided answer is still there, because that came from `fit_profile`
(SQLite), never from the session state that button just cleared.

## 3 — Proof it doesn't leak the other way

A third, genuinely fresh session, asking the *same* referential
follow-up from §1 with no prior search in this session:
```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"mem-demo-C","customer_id":"CUST-PRIYA","message":"the second one, in my size"}'
```
**Captured:** `{"type":"clarify","intent_guess":"unknown","confidence":0.35,...}` — correctly can't resolve "the second one" with nothing to reference. This isn't a hardcoded rule; the Orchestrator passes a `has_recent_results` flag (from this session's own Redis/SQLite lookup) into intent classification, and it's honestly `false` here — session A's list never enters the picture.

**From the UI:** click "New session" again (or reload the page fresh)
and, *before searching for anything*, type `the second one, in my
size` straight into the search bar. Expect the same clarifying banner
as above, not a wrong or hallucinated product — proof the reset in §2
wasn't cosmetic.

> **Note on verification:** the three curl sequences above were run
> and captured live while writing this document. The UI steps weren't
> — no browser tooling was available this session to click through
> them directly. They're not guessed, though: every UI action listed
> maps to the exact code path already proven by curl (the search bar
> calls the same `/chat` endpoint, "New session" is a two-line
> `session_id` swap in `streamlit_app.py`), so this is a translation of
> verified behavior into UI steps, not a new, unverified claim. Worth
> an actual click-through before relying on it in front of an
> audience.

---

**In one line:** short-term memory is disposable (TTL'd, per-session,
holds what was just shown); long-term memory is the customer's real
tables (never expires, per-customer, is what makes them "the same
customer" on day two). Nothing durable ever comes from Redis, and
nothing session-scoped ever crosses a `session_id` boundary — both
shown above, not just claimed.
