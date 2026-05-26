-- Phase 3: chore schema simplified to tracking-only.
-- No recurrence, no assignment, no streaks — just who did what and when.
-- Drops and recreates both chore tables; safe because no production data exists yet.

DROP TABLE IF EXISTS chore_completions;
DROP TABLE IF EXISTS chores;

CREATE TABLE chores (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name         TEXT    NOT NULL,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(household_id, name)
);

CREATE TABLE chore_completions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chore_id     INTEGER NOT NULL REFERENCES chores(id),
    user_id      INTEGER NOT NULL REFERENCES users(id),
    completed_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
