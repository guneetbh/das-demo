-- Neu.Tail — embedded Data Universe (§04 of the architecture doc)
-- One SQLite file, nine tables, plus session_context for short-term memory (§07).

CREATE TABLE IF NOT EXISTS customers (
    customer_id     TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    email           TEXT,
    tenure_months   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS loyalty (
    customer_id     TEXT PRIMARY KEY REFERENCES customers(customer_id),
    tier            TEXT NOT NULL CHECK (tier IN ('Bronze','Silver','Gold','Platinum')),
    points_balance  INTEGER NOT NULL DEFAULT 0,
    ytd_spend       REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS catalogue (
    sku             TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    price           REAL NOT NULL,
    tier            TEXT NOT NULL CHECK (tier IN ('premium','private_label')),
    trending        INTEGER NOT NULL DEFAULT 0,      -- SCOUT's signal, pre-seeded per §01
    occasion_tags   TEXT NOT NULL DEFAULT '',         -- comma-separated, e.g. "date-night,evening"
    image_url       TEXT NOT NULL DEFAULT ''          -- dummy placeholder, generated at seed time — no real product photography
);

CREATE TABLE IF NOT EXISTS inventory (
    sku             TEXT NOT NULL REFERENCES catalogue(sku),
    size            TEXT NOT NULL,
    location        TEXT NOT NULL DEFAULT 'DC1',
    stock_qty       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (sku, size, location)
);

CREATE TABLE IF NOT EXISTS transactional (
    order_id        TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    sku             TEXT REFERENCES catalogue(sku),
    kind            TEXT NOT NULL CHECK (kind IN ('order','subscription')),
    amount          REAL NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS returns (
    return_id       TEXT PRIMARY KEY,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    sku             TEXT NOT NULL REFERENCES catalogue(sku),
    size_returned   TEXT NOT NULL,
    reason_code     TEXT NOT NULL,                    -- GRADE-graded once at seed time, per §01
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fit_profile (
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    category        TEXT NOT NULL,
    preferred_size  TEXT,
    runs            TEXT CHECK (runs IN ('small','true','large')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),  -- when guidance was established — lets admin.py compare an order's date against this instead of a cross-sectional has/hasn't split
    PRIMARY KEY (customer_id, category)
);

CREATE TABLE IF NOT EXISTS behavioural (
    event_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    event_type      TEXT NOT NULL CHECK (event_type IN ('browse','search','cart')),
    sku             TEXT REFERENCES catalogue(sku),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS escalations (
    escalation_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    kind            TEXT NOT NULL,                     -- 'subscription' or 'order'
    amount          REAL NOT NULL,
    sku             TEXT REFERENCES catalogue(sku),     -- set for 'order' escalations, NULL for 'subscription'
    reason          TEXT NOT NULL,                     -- why SENTRY escalated instead of approving
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at     TEXT,
    resolved_by     TEXT
);

-- Short-term (session) memory — written back by the Orchestrator at end of turn, per §07.
CREATE TABLE IF NOT EXISTS session_context (
    session_id      TEXT NOT NULL,
    key             TEXT NOT NULL,
    value           TEXT NOT NULL,                     -- JSON-encoded
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (session_id, key)
);

-- Audit trail — every tool call and model call, policy-checked, per §05.
CREATE TABLE IF NOT EXISTS audit_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    caller          TEXT NOT NULL,
    tool            TEXT NOT NULL,
    allowed         INTEGER NOT NULL,
    detail          TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);
