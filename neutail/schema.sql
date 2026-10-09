-- Neu.Tail — embedded data universe. One SQLite file is the system of record
-- for every domain in the deck's slide-7 matrix; Chroma (vectors) and Redis
-- (session memory) are optional accelerators on top of it.
-- All data seeded into this schema is synthetic.
-- ---------------------------------------------------------------- CRM

CREATE TABLE IF NOT EXISTS customers (
    customer_id        TEXT PRIMARY KEY,
    name               TEXT NOT NULL,
    email              TEXT NOT NULL UNIQUE,
    city               TEXT NOT NULL DEFAULT 'London',
    tenure_months      INTEGER NOT NULL DEFAULT 0,
    marketing_consent  INTEGER NOT NULL DEFAULT 1,      -- PII & consent policy: no upsell without it
    persona_blurb      TEXT NOT NULL DEFAULT '',        -- demo picker copy only
    created_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- Loyalty
CREATE TABLE IF NOT EXISTS loyalty (
    customer_id     TEXT PRIMARY KEY REFERENCES customers(customer_id),
    tier            TEXT NOT NULL CHECK (tier IN ('Bronze','Silver','Gold','Platinum')),
    points_balance  INTEGER NOT NULL DEFAULT 0 CHECK (points_balance >= 0),
    ytd_spend       REAL NOT NULL DEFAULT 0
);

-- Append-only; points_balance is the running total of this ledger.

-- UNIQUE(source, ref_id) makes every earn/redeem idempotent per business event.

CREATE TABLE IF NOT EXISTS loyalty_ledger (
    entry_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    delta        INTEGER NOT NULL,
    source       TEXT NOT NULL,          -- order | subscription | redeem | seed | challenge
    ref_id       TEXT NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, ref_id)
);

-- ---------------------------------------------------------------- Catalogue

CREATE TABLE IF NOT EXISTS catalogue (
    sku             TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    price           REAL NOT NULL CHECK (price > 0),
    was_price       REAL,                                -- set when PACE/CLEAR has marked it down
    tier            TEXT NOT NULL CHECK (tier IN ('premium','private_label')),
    trending        INTEGER NOT NULL DEFAULT 0,          -- SCOUT's seeded signal
    occasion_tags   TEXT NOT NULL DEFAULT '',
    colour          TEXT NOT NULL DEFAULT '',
    material        TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    image_url       TEXT NOT NULL DEFAULT '',            -- relative, e.g. /assets/products/dress-red-gown.jpg
    is_new          INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plans (
    plan           TEXT PRIMARY KEY,
    display_name   TEXT NOT NULL,
    monthly_price  REAL NOT NULL CHECK (monthly_price > 0),
    perks          TEXT NOT NULL DEFAULT ''
);

-- ---------------------------------------------------------------- Inventory

CREATE TABLE IF NOT EXISTS inventory (
    sku         TEXT NOT NULL REFERENCES catalogue(sku),
    size        TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT 'DC1',
    stock_qty   INTEGER NOT NULL DEFAULT 0 CHECK (stock_qty >= 0),
    PRIMARY KEY (sku, size, location)
);

-- ---------------------------------------------------------------- Transactional

CREATE TABLE IF NOT EXISTS transactional (
    order_id         TEXT PRIMARY KEY,
    customer_id      TEXT NOT NULL REFERENCES customers(customer_id),
    sku              TEXT REFERENCES catalogue(sku),
    size             TEXT,
    quantity         INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    kind             TEXT NOT NULL CHECK (kind IN ('order','subscription')),
    plan             TEXT REFERENCES plans(plan),
    amount           REAL NOT NULL CHECK (amount > 0),
    idempotency_key  TEXT UNIQUE,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- CONCIERGE offers awaiting an explicit customer decision. Composing an offer

-- never moves money; only accept_offer can.

CREATE TABLE IF NOT EXISTS offers (
    offer_id     TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    plan         TEXT NOT NULL REFERENCES plans(plan),
    amount       REAL NOT NULL CHECK (amount > 0),
    offer_text   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open'
                 CHECK (status IN ('open','accepted','pending_review','declined','expired')),
    order_id     TEXT,
    expires_at   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS escalations (
    escalation_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      TEXT NOT NULL REFERENCES customers(customer_id),
    kind             TEXT NOT NULL CHECK (kind IN ('subscription','order')),
    amount           REAL NOT NULL CHECK (amount > 0),
    sku              TEXT REFERENCES catalogue(sku),
    size             TEXT,
    quantity         INTEGER NOT NULL DEFAULT 1,
    plan             TEXT REFERENCES plans(plan),
    offer_id         TEXT REFERENCES offers(offer_id),
    risk_score       REAL NOT NULL DEFAULT 0,
    reason           TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','denied')),
    idempotency_key  TEXT UNIQUE,
    order_id         TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at      TEXT,
    resolved_by      TEXT
);

-- ---------------------------------------------------------------- Returns (GRADE)

CREATE TABLE IF NOT EXISTS returns (
    return_id       TEXT PRIMARY KEY,
    order_id        TEXT REFERENCES transactional(order_id),
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    sku             TEXT NOT NULL REFERENCES catalogue(sku),
    size_returned   TEXT NOT NULL,
    reason_code     TEXT NOT NULL CHECK (reason_code IN
                    ('too_small','too_large','not_as_described','changed_mind','defective')),
    condition_grade TEXT NOT NULL DEFAULT 'A' CHECK (condition_grade IN ('A','B','C')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fit_profile (
    customer_id     TEXT NOT NULL REFERENCES customers(customer_id),
    category        TEXT NOT NULL,
    preferred_size  TEXT,
    runs            TEXT CHECK (runs IN ('small','true','large')),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (customer_id, category)
);

-- ---------------------------------------------------------------- Behavioural

-- 'impression' = shown to the customer without them asking (recommendation

-- grids). Kept apart from 'search'/'browse'/'cart' so a recommendation can

-- never boost its own category the next day.

CREATE TABLE IF NOT EXISTS behavioural (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id  TEXT NOT NULL REFERENCES customers(customer_id),
    event_type   TEXT NOT NULL CHECK (event_type IN ('browse','search','cart','impression','fit_check')),
    sku          TEXT REFERENCES catalogue(sku),
    query        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- Short-term memory (SQLite fallback for Redis)

CREATE TABLE IF NOT EXISTS session_context (
    customer_id  TEXT NOT NULL,
    session_id   TEXT NOT NULL,
    value        TEXT NOT NULL,                        -- JSON-encoded context object
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (customer_id, session_id)
);

-- ---------------------------------------------------------------- Audit / observability

CREATE TABLE IF NOT EXISTS audit_log (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id        TEXT,
    kind            TEXT NOT NULL DEFAULT 'tool',      -- tool | model | retrieval | human | api
    caller          TEXT NOT NULL,
    actor           TEXT,                              -- who started the request (hashed customer, reviewer, ...)
    tool            TEXT NOT NULL,
    allowed         INTEGER NOT NULL,
    outcome         TEXT NOT NULL DEFAULT 'ok',        -- ok | denied | invalid | error | fallback
    latency_ms      INTEGER,
    model           TEXT,
    prompt_version  TEXT,
    detail          TEXT,                              -- redacted JSON
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- Indexes

CREATE INDEX IF NOT EXISTS ix_beh_cust_time   ON behavioural(customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_beh_time        ON behavioural(created_at);
CREATE INDEX IF NOT EXISTS ix_ret_cust_sku    ON returns(customer_id, sku);
CREATE INDEX IF NOT EXISTS ix_ret_sku         ON returns(sku);
CREATE INDEX IF NOT EXISTS ix_tx_cust         ON transactional(customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_tx_sku          ON transactional(sku);
CREATE INDEX IF NOT EXISTS ix_esc_status      ON escalations(status);
CREATE INDEX IF NOT EXISTS ix_cat_category    ON catalogue(category);
CREATE INDEX IF NOT EXISTS ix_audit_trace     ON audit_log(trace_id);
CREATE INDEX IF NOT EXISTS ix_ledger_cust     ON loyalty_ledger(customer_id, created_at);
CREATE INDEX IF NOT EXISTS ix_offers_cust     ON offers(customer_id, status);