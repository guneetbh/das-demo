# Neu.Tail — Personalisation Assistant

Python + SQLite implementation of the Mission 3 agentic ecosystem: the Lead Orchestrator,

the seven Personalisation agents (PERSONA, MUSE, TAILOR, CARE, CONCIERGE, SENTRY, TALLY), thin

stubs for ONEBASKET / SCOUT / PACE / GRADE, and the shared agent platform from the deck:

runtime, policy-as-code, model gateway, prompt service, two-tier memory, vector retrieval, audit.

A Streamlit storefront, reviewer and admin consoles, and an authenticated MCP adapter sit on top.

Everything runs locally with zero external services; live models, Redis and Chroma are optional.

| Document | What it's for |
|---|---|
| [`docs/solution-architecture.html`](docs/solution-architecture.html) | Interactive end-to-end architecture: business context, numbered flows, NFRs, ADRs (open in a browser) |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Architecture notes (as built) |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | 15-minute presenter's demo script |
| [`docs/CORRECTIONS-AND-TARGET-ARCHITECTURE.md`](docs/CORRECTIONS-AND-TARGET-ARCHITECTURE.md) | The review that drove the build, with implementation status |
| [`docs/deck/`](docs/deck/) | Mission 3 service-blueprint deck (source of the architecture) |

---

## Project structure

```

das-demo/

├── pyproject.toml              package metadata + dependencies (single source of truth)
├── requirements.txt            convenience: installs the package with all extras
├── README.md
│
├── neutail/                    the Python package
│   ├── config.py               settings, env overrides, money formatting
│   ├── db.py · schema.sql      SQLite connection, unit of work, schema
│   ├── context.py              per-turn context object
│   ├── seed.py                 synthetic data generator   (python -m neutail.seed)
│   ├── reporting.py            business outcomes          (python -m neutail.reporting)
│   ├── api/                    Experience API (FastAPI)   (uvicorn neutail.api:app)
│   │   ├── app.py              routes, error mapping, Context per request
│   │   └── auth.py             shopper tokens, staff API keys → principal
│   ├── orchestration/          Lead Orchestrator layer
│   │   ├── orchestrator.py     route · decompose · confidence gate · memory
│   │   ├── intents.py          intent + entity classification (model or fallback)
│   │   └── response_composer.py
│   ├── agents/                 persona · muse · tailor · care · concierge · sentry · tally · stubs
│   ├── platform/               runtime · contracts · policy · audit · gateway · prompts · session_store · vector_store
│   ├── data/                   per-domain data access (enforces the agent × domain matrix)
│   ├── prompts/                versioned prompt YAML + shared guard prompt
│   └── transports/
│       └── mcp_server.py       external, authenticated MCP adapter (python -m neutail.transports.mcp_server)
│
├── apps/
│   └── storefront/
│       └── streamlit_app.py    shopper storefront + review and admin consoles
├── scripts/
│   ├── run_demo.py             the whole demo story in the terminal, no servers
│   └── demo_mcp_client.py      external MCP client demo
├── tests/                      pytest suite (throwaway DB, no network)
├── config/
│   └── models.json             capability → model map (reasoning / fast / vision / embedding)
├── assets/
│   ├── logo-*.png
│   └── products/               63 product photos + CREDITS.md (Unsplash License)
├── docs/
│   ├── solution-architecture.html · ARCHITECTURE.md · RUNBOOK.md · CORRECTIONS-AND-TARGET-ARCHITECTURE.md
│   ├── deck/                   Mission 3 deck (.pptx)
│   └── archive/                pre-refactor runbooks and diagrams (history only)
├── .streamlit/config.toml      storefront theme (brand colours)
└── data/                       generated at runtime: neutail.db, chroma/ (git-ignored)
```

Dependencies point downwards only: `apps` → HTTP → `api` → `orchestration` → `agents` →

`platform` → `data` → `db`. The storefront talks to the API over HTTP and imports nothing from `neutail`.

---

## Step-by-step: run the project

Run every command from the repository root (`das-demo/`).

### Step 1 — Check the prerequisites

| Need | Version | Check with |
|---|---|---|
| Python | 3.10 or newer | `python --version` (macOS/Linux: `python3 --version`) |
| pip | any recent | `python -m pip --version` |
| Git | any | `git --version` |
| A browser | Chrome, Edge, Firefox or Safari | for the storefront and the architecture page |

Nothing else is required: no database server, no Docker, no API key.

> On macOS/Linux use `python3` wherever this guide says `python` if `python` isn't on your PATH.

### Step 2 — Get the code

```bash

git clone <repo-url> das-demo

cd das-demo

git checkout feature/target-architecture

```

### Step 3 — Create and activate a virtual environment

```bash

python -m venv .venv

```

| Shell | Activate with |
|---|---|
| Windows PowerShell | `.\.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |
| Windows Git Bash | `source .venv/Scripts/activate` |
| macOS / Linux | `source .venv/bin/activate` |

Your prompt now starts with `(.venv)`. Activate it again in every new terminal you open below.

> PowerShell says scripts are disabled? Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.

### Step 4 — Install the project

```bash

python -m pip install --upgrade pip

python -m pip install -e ".[all]"

```

This installs `neutail` in editable mode with the storefront, live-model client, Redis client,

MCP and test tools. `python -m pip install -r requirements.txt` does exactly the same.

Smaller installs if you prefer:

| You want | Command |
|---|---|
| API + agents only | `python -m pip install -e .` |
| + storefront | `python -m pip install -e ".[ui]"` |
| + tests | `python -m pip install -e ".[ui,dev]"` |
| + embedding search (Chroma, ~80 MB model on first use) | `python -m pip install -e ".[vectors]"` |

> Keep the install editable (`-e`). The app finds `assets/`, `config/` and `data/` relative to the repository.

### Step 5 — (Optional) configure live models and secrets

The project runs fully without this step. To let routing, ranking, stylist pitches and fit notes

use live Claude models, create `.env` in the repository root (it's git-ignored):

```dotenv

# Either key works; OpenRouter is tried first if both are set

ANTHROPIC_API_KEY=sk-ant-...
# OPENROUTER_API_KEY=sk-or-...

# Override these anywhere the app is shared (demo defaults otherwise)
# NEUTAIL_TOKEN_SECRET=change-me
# NEUTAIL_REVIEWER_KEY=change-me
# NEUTAIL_ADMIN_KEY=change-me
# NEUTAIL_MCP_KEY=change-me

# Optional services
# REDIS_URL=redis://localhost:6379/0

```

Models are set in [`config/models.json`](config/models.json): reasoning and vision use `claude-opus-5-5`,

fast uses `claude-haiku-4-5`.

### Step 6 — Seed the database

```bash

python -m neutail.seed

```

Expected output (about one second):

```

Seeded 40 customers (6 demo personas), 1096 SKUs with photos, 406 orders, 125 returns (30.8%), 395 behavioural events, 4 escalations.

Vector index built (fallback backend): 1096 SKUs.

```

This creates `data/neutail.db`. Re-run it any time to reset to a clean estate; the API can stay running.

### Step 7 — Run the tests

```bash

python -m pytest

```

Expected: `28 passed`. Tests use a throwaway database and never call a live model, so they don't

touch the data from Step 6.

### Step 8 — Run the demo in the terminal (no servers)

```bash

python scripts/run_demo.py

```

This walks the whole story in about a second: personalised discovery for two personas, a

follow-up from session memory, fit guidance from return history, the consent-based stylist

offer, an escalation to human review, a purchase that upgrades a tier, denied forged calls, and a

full audit trace. It changes data (e.g. Sam becomes Gold), so **re-seed afterwards**:

```bash

python -m neutail.seed

```

### Step 9 — Start the Experience API (terminal 1)

```bash

python -m uvicorn neutail.api:app --port 8000

```

Leave it running. Check it from another terminal or a browser:

```bash

curl http://127.0.0.1:8000/health

```

```json

{"status":"ok","session_backend":"sqlite","vector_backend":"fallback","live_model_providers":[],"currency":"GBP"}

```

`live_model_providers` lists any API keys found in Step 5. Interactive API docs are at

http://127.0.0.1:8000/docs. Add `--reload` while developing.

### Step 10 — Start the storefront (terminal 2)

Activate the virtual environment in the new terminal (Step 3), then:

```bash

python -m streamlit run apps/storefront/streamlit_app.py

```

Streamlit opens http://localhost:8501. If it asks for an email on first run, press Enter to skip,

or add `--server.headless true`. Start it from the repository root so the brand theme in

`.streamlit/config.toml` is picked up.

### Step 11 — Use the storefront

1. **Pick a persona** in the sidebar's *Shopping as* box. Each has a caption saying what it demonstrates.

2. **Home page**: *Recommended for you* (with reasons and personal return risk), recently viewed, trending, on sale and new in.

3. **Search or click a suggestion**, e.g. `something for date night`, `show me blazers and check my fit for jeans`,

   `I'd like a stylist`, `how many points do I have?`. Open *Behind the scenes* under a result to see segment, intents and trace id.

4. **Open a product**: choose a size, *Check my fit* (pre-selects the recommended size), *Buy now*, or *Talk to a stylist*.

   A stylist offer charges nothing until you click **Subscribe**.

5. **📦 My orders**: return an item as *Too small*, then check fit for that category again. TAILOR learns from GRADE.

6. **Sidebar → 🎁 Redeem points** to spend points on a reward.

7. **🛡️ Human review**: approve or deny charges SENTRY paused (try Amara + *I'd like a stylist* + Subscribe first).

8. **📊 Admin**: business outcomes, a trace of the last request, vector search, the enforced policy matrix, contracts, prompts, models and the redacted audit trail.

The full presenter script is in [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

### Step 12 — Try the API directly (optional)

Shopper calls need a token from `/auth/login`; staff calls use an `X-API-Key` header.

**Git Bash / macOS / Linux**

```bash

TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login -H "Content-Type: application/json" \

  -d '{"handle":"CUST-PRIYA"}' | python -c "import sys,json; print(json.load(sys.stdin)['token'])")

curl -s -X POST http://127.0.0.1:8000/chat -H "Authorization: Bearer $TOKEN" \

  -H "Content-Type: application/json" -d '{"session_id":"s1","message":"check my fit for jeans"}'

curl -s http://127.0.0.1:8000/admin/outcomes -H "X-API-Key: dev-admin-key"

```

**Windows PowerShell**

```powershell

$login = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/auth/login -ContentType "application/json" -Body '{"handle":"CUST-PRIYA"}'

$h = @{ Authorization = "Bearer $($login.token)" }

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/chat -Headers $h -ContentType "application/json" -Body '{"session_id":"s1","message":"check my fit for jeans"}'

Invoke-RestMethod -Uri http://127.0.0.1:8000/admin/outcomes -Headers @{ "X-API-Key" = "dev-admin-key" }

```

Demo customer IDs: `CUST-PRIYA`, `CUST-JORDAN`, `CUST-AMARA`, `CUST-SAM`, `CUST-ELENA`, `CUST-KENJI`.

Default staff keys: `dev-reviewer-key`, `dev-admin-key`, `dev-mcp-key`.

### Step 13 — Run the external MCP adapter (optional, terminal 3)

```bash

python -m neutail.transports.mcp_server      # MCP over SSE on :8765, Bearer key required

python scripts/demo_mcp_client.py            # in another terminal

```

The client lists the tools, makes an allowed read, and shows that writes (and a self-declared

`caller`) are denied. To connect Claude Desktop or Claude Code, add an SSE MCP server at

`http://127.0.0.1:8765/sse` with header `Authorization: Bearer dev-mcp-key`.

### Step 14 — Turn on the optional services (optional)

| Service | How | Effect |
|---|---|---|
| Live models | add a key to `.env` (Step 5), restart the API | routing, search ranking, stylist pitches and fit notes use Claude; `/health` lists the provider |
| Redis | run `redis-server` (or `docker run -p 6379:6379 redis`), restart the API | session memory moves to Redis with a 30-min TTL; `/health` shows `session_backend: redis` |
| Chroma | `python -m pip install -e ".[vectors]"`, then `python -m neutail.seed` | semantic search uses local embeddings (cosine); first use downloads an ~80 MB model |

### Step 15 — Open the architecture page

Open `docs/solution-architecture.html` in a browser (double-click it; no server needed). Pick a

flow and click the numbered badges to zoom into each step.

### Step 16 — Stop everything

Press `Ctrl+C` in each terminal (API, storefront, MCP adapter), then run `deactivate`.

---

## Command cheat sheet

| Task | Command |
|---|---|
| Install | `python -m pip install -e ".[all]"` |
| Reset data | `python -m neutail.seed` |
| Tests | `python -m pytest` |
| Terminal demo | `python scripts/run_demo.py` |
| API | `python -m uvicorn neutail.api:app --port 8000` |
| Storefront | `python -m streamlit run apps/storefront/streamlit_app.py` |
| MCP adapter / client | `python -m neutail.transports.mcp_server` / `python scripts/demo_mcp_client.py` |
| Business outcomes (JSON) | `python -m neutail.reporting` |

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Storefront says *Can't reach the Experience API* | Start Step 9 first and keep that terminal open. If the API runs elsewhere, set `NEUTAIL_API_BASE` before starting Streamlit. |
| `address already in use` / port 8000 or 8501 busy | Stop the other process (Windows: `netstat -ano \| findstr :8000`, then `taskkill /PID <pid> /F`; macOS/Linux: `lsof -i :8000`), or use `--port 8001` and set `NEUTAIL_API_BASE=http://127.0.0.1:8001`. |
| `ModuleNotFoundError: neutail` or `fastapi` | The virtual environment isn't active in that terminal (Step 3), or Step 4 didn't run. |
| `No module named pytest` / `streamlit` | Install the extras: `python -m pip install -e ".[all]"`. |
| `no such table` or empty storefront | Run `python -m neutail.seed` (Step 6). |
| Demo data looks "used" (Sam already Gold, review queue changed) | Re-seed: `python -m neutail.seed`. |
| Storefront has no brand colours | Start Streamlit from the repository root so it finds `.streamlit/config.toml`. |
| Results say *template* / `used_live_model: false` | Expected without an API key. With a key, check `/health` and Admin → audit rows for `model_gateway` errors (wrong key, billing or model ID). |
| `401` from the API after restarting it | The storefront logs in again automatically; for curl, request a new token. |
| `403` on an API call | Working as designed: that principal isn't allowed that tool (see Admin → Policy). |
| Garbled `£` / arrows in a Windows terminal | Use Windows Terminal or run `chcp 65001`; the scripts already switch their own output to UTF-8. |

---

## The seeded estate

All synthetic. 40 customers, 1,096 SKUs in seven categories, 12 months of orders with sizes,

returns linked to orders, browsing, a points ledger, plans and a review queue. Every SKU has a

real product photo matched to its type (boots show boots, clutches show clutches), and its colour

attribute comes from the photo. Six demo personas, each showing something different:

| Persona | Shows |
|---|---|
| Priya Nair · Gold | affluent ranking; jeans run small (GRADE → TAILOR recommends L); Neu.Tail+ offer approved |
| Jordan Lee · Bronze | new customer, value ranking, no fit history, first-time-payer risk |
| Amara Okafor · Platinum | Elite £90 offer → SENTRY escalates → human review |
| Sam Patel · Silver | £150 from Gold — a purchase triggers a tier upgrade |
| Elena Rossi · Gold | no marketing consent → CARE blocks the upsell; shoes run large |
| Kenji Tanaka · Silver | lapsing micro-segment |

Returns are simulated causally (wrong size → likely fit return; guidance → right size), giving a

~35% baseline and ~14% guided return rate in **Admin → Business outcomes**.

## HTTP API (selection)

| Route | Auth | What |
|---|---|---|
| `POST /auth/login` | — | `{handle}` → shopper token (demo persona login) |
| `POST /chat` | shopper | orchestrator turn: discovery · fit · service · loyalty · accept_offer |
| `GET /recommendations` | shopper | home-page / similar-item rows (impressions) |
| `POST /orders`, `GET /orders` | shopper | purchase (idempotent), history |
| `POST /offers/{id}/accept` | shopper | the only way a subscription is charged |
| `POST /fit`, `POST /returns` | shopper | TAILOR guidance; GRADE return |
| `GET /loyalty`, `POST /loyalty/redeem` | shopper | TALLY status, badges, rewards |
| `GET /products`, `GET /products/{sku}` | — | public catalogue |
| `GET /escalations`, `POST /escalations/{id}/resolve` | reviewer key | human review |
| `GET /audit`, `/admin/traces/{id}`, `/admin/outcomes`, `/admin/policy`, `/admin/vector_search`, `POST /admin/reindex` | admin key | observability |
| `POST /tools/{name}/invoke` | staff key | testing; caller = the key's principal |