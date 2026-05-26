-- Roomagent initial schema
-- Dates stored as ISO-8601 TEXT. Booleans stored as INTEGER (0/1).

CREATE TABLE IF NOT EXISTS households (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_chat_id INTEGER UNIQUE NOT NULL,
    name             TEXT    NOT NULL,
    created_at       TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- One row per roommate. rent_share_pct is their fraction of rent (0.0–1.0).
-- All shares in a household should sum to 1.0 once everyone has joined.
CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id     INTEGER NOT NULL REFERENCES households(id),
    telegram_user_id INTEGER NOT NULL,
    name             TEXT    NOT NULL,
    venmo_handle     TEXT,
    zelle_email      TEXT,
    rent_share_pct   REAL    NOT NULL DEFAULT 0,
    UNIQUE(household_id, telegram_user_id)
);

-- Every supply purchase. amount_cents avoids floating-point money bugs.
CREATE TABLE IF NOT EXISTS purchases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    buyer_id     INTEGER NOT NULL REFERENCES users(id),
    item         TEXT    NOT NULL,
    category     TEXT,
    amount_cents INTEGER NOT NULL,
    note         TEXT,
    purchased_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Supply inventory. Derived from purchases + manual flags.
CREATE TABLE IF NOT EXISTS supplies (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id         INTEGER NOT NULL REFERENCES households(id),
    name                 TEXT    NOT NULL,
    last_purchased_at    TEXT,
    typical_days_between INTEGER,
    low_flag             INTEGER NOT NULL DEFAULT 0,
    UNIQUE(household_id, name)
);

-- Recurring bills. Rent lives here with is_rent=1.
CREATE TABLE IF NOT EXISTS recurring_bills (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name         TEXT    NOT NULL,
    amount_cents INTEGER NOT NULL,
    due_day      INTEGER NOT NULL,  -- day of month (1-31)
    split_method TEXT    NOT NULL DEFAULT 'equal',
    is_rent      INTEGER NOT NULL DEFAULT 0
);

-- One-off bills (utilities, etc.)
CREATE TABLE IF NOT EXISTS bills (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id    INTEGER NOT NULL REFERENCES households(id),
    type            TEXT    NOT NULL,
    amount_cents    INTEGER NOT NULL,
    due_date        TEXT,
    paid_by_user_id INTEGER NOT NULL REFERENCES users(id),
    split_method    TEXT    NOT NULL DEFAULT 'equal',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Per-person share of a bill. paid=1 means they've settled up.
CREATE TABLE IF NOT EXISTS bill_shares (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id      INTEGER NOT NULL REFERENCES bills(id),
    user_id      INTEGER NOT NULL REFERENCES users(id),
    amount_cents INTEGER NOT NULL,
    paid         INTEGER NOT NULL DEFAULT 0
);

-- Chores schema — schema-ready for Phase 3, no handlers built yet.
CREATE TABLE IF NOT EXISTS chores (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id      INTEGER NOT NULL REFERENCES households(id),
    name              TEXT    NOT NULL,
    recurrence_rule   TEXT,
    assignee_strategy TEXT    NOT NULL DEFAULT 'round_robin'
);

CREATE TABLE IF NOT EXISTS chore_completions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    chore_id             INTEGER NOT NULL REFERENCES chores(id),
    assigned_to_user_id  INTEGER REFERENCES users(id),
    completed_by_user_id INTEGER REFERENCES users(id),
    completed_at         TEXT
);

-- Audit log for every mutation. /undo reads this to reverse actions.
-- payload_json stores enough info to reverse the action (e.g. the row id that was inserted).
CREATE TABLE IF NOT EXISTS actions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    user_id      INTEGER NOT NULL REFERENCES users(id),
    action_type  TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    reversed_at  TEXT    -- set when /undo is called; NULL means active
);
