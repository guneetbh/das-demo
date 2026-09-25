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

## 3 — Proof it doesn't leak the other way

A third, genuinely fresh session, asking the *same* referential
follow-up from §1 with no prior search in this session:
```bash
curl -s -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"session_id":"mem-demo-C","customer_id":"CUST-PRIYA","message":"the second one, in my size"}'
```
**Captured:** `{"type":"clarify","intent_guess":"unknown","confidence":0.35,...}` — correctly can't resolve "the second one" with nothing to reference. This isn't a hardcoded rule; the Orchestrator passes a `has_recent_results` flag (from this session's own Redis/SQLite lookup) into intent classification, and it's honestly `false` here — session A's list never enters the picture.

---

**In one line:** short-term memory is disposable (TTL'd, per-session,
holds what was just shown); long-term memory is the customer's real
tables (never expires, per-customer, is what makes them "the same
customer" on day two). Nothing durable ever comes from Redis, and
nothing session-scoped ever crosses a `session_id` boundary — both
shown above, not just claimed.
