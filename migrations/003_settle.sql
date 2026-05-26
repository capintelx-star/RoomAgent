-- Phase 4: settle-up support.
-- settled_at NULL = active (included in balance computations).
-- settled_at IS NOT NULL = marked settled by /settle confirm; excluded from balances.

ALTER TABLE actions ADD COLUMN settled_at TEXT;
