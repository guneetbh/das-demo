**# Demo runbook (≈15 minutes)**

```bash

python -m neutail.seed

python -m uvicorn neutail.api:app --port 8000               # terminal 1

python -m streamlit run apps/storefront/streamlit_app.py     # terminal 2 → [http://localhost:8501](http://localhost:8501)

```

Re-seed between rehearsals (`python -m neutail.seed`; the API can stay running). No-UI version of

the same script: `python scripts/run_demo.py`.

| # | Do | Point out |
|---|---|---|
| 1 | Land as ****Priya****. | "Welcome back" + Recommended for you, from her favourite categories; each card has a reason and her **personal** return risk. Sidebar: Gold, points, progress to Platinum, badges. |
| 2 | Switch to ****Jordan****. | Same estate, value segment: private-label first, no fit history. |
| 3 | As Priya, click ****something for date night****. | MUSE results; open ****Behind the scenes**** → segment, intents, trace id. |
| 4 | Type ****the second one, in my size****. | Follow-up resolved from session memory; no context restated. |
| 5 | Type ****show me blazers and check my fit for jeans****. | Multi-intent: discovery + fit, and fit sizes ****jeans**** (named), not the first blazer. TAILOR: "go with L … runs small", risk ≈16% vs ≈36% baseline. |
| 6 | Open a product → ****Check my fit****, add a note chip. | Recommended size pre-selected in the size picker; note blended by the fast model (or template fallback). |
| 7 | ****Talk to a stylist****. | Offer card with price and perks, and ****nothing charged****. Click ****Subscribe**** → SENTRY approves → order and points land together. |
| 8 | Switch to ****Amara****, ask for a stylist, Subscribe. | Elite £90 > £75 bound → "a colleague will confirm". ****Human review**** tab → Approve → order + points in one transaction. Approve twice? The second fails. |
| 9 | Switch to ****Elena****, ask for a stylist. | No offer: CARE blocks it because there's no marketing consent on file. |
| 10 | Switch to ****Sam****, buy the ****Cropped Check Blazer**** (£214). | "🎉 you're now Gold" — TALLY tier upgrade from YTD spend. Click Buy again → same order (idempotency). |
| 11 | ****My orders**** → return something as **Too small**, then check fit for that category. | GRADE → TAILOR loop: guidance and risk change. |
| 12 | Sidebar → ****Redeem points****. | Ledger debit, voucher code, can't overdraw. |
| 13 | ****Admin**** tab. | Outcomes (≈35% → ≈14% returns), ****Trace**** for the last request (every agent hop with latency/model/prompt version), the enforced data matrix, contracts, prompts, models, redacted audit. |
| 14 | Optional: `python -m neutail.transports.mcp_server` + `python scripts/demo_mcp_client.py`. | External MCP: no key → 401; reads OK; writes and a self-declared `caller` are denied. |