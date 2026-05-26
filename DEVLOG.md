# Roomagent — Dev Log

Deviations from the original spec and their rationale.

---

## 2026-05-13 — Phase 0 scaffold

**No deviations.** Spec followed exactly.

Notes:
- Python 3.14.3 in use (spec said "use whatever version is installed"). pyproject.toml sets `requires-python = ">=3.11"`.
- APScheduler pinned to `<4` to stay on the stable v3 API. The v4 API changed significantly and docs are sparse.
- `pytest-asyncio` added to dev dependencies. Needed to run async test functions without boilerplate. Not an extra dependency — pure test tooling.
- `[project.scripts]` entry `roomagent = "bot:main"` added for convenience. After `pip install -e .`, running `roomagent` starts the bot without needing to set `PYTHONPATH` manually.

---

## 2026-05-13 — Phase 2 bills & balances

**Deviations:**

1. **Equal splits only for Phase 2.** The spec mentions `rent_share_pct` for custom rent splits. Implementing equal-split only for MVP; custom percentages deferred to Phase 4 polish. The column exists in the schema and the `recurring_bills.split_method` field is plumbed through.

2. **Rent reminders skip due_day ≤ 3.** The 3-day early reminder is computed as `due_day - today.day`. If due_day is 1, 2, or 3, the 3-day notice falls in the prior month and `days_until` would be negative on the right day. Skipped for MVP; a proper month-boundary calculation would use `calendar.monthrange`.

3. **`/owe` DM fallback added beyond spec.** Spec says "DMs the caller". Added explicit handling for `telegram.error.Forbidden` (raised when the user has never messaged the bot) with a group-chat fallback message. Required for correct UX — Telegram blocks bots from initiating DMs.

4. **`compute_balances` in splits.py bug fixed.** The Phase 0 implementation did not credit the bill payer in the balance computation, causing balances to not sum to zero. Fixed by crediting `paid_by_user_id` for each unpaid bill_share. Tests updated accordingly.

---

## 2026-05-13 — Phase 3 chores (tracking-only)

**Scope reduction from original plan:**

Phase 3 was redesigned mid-session to be tracking-only: no recurrence rules, no
round-robin assignment, no daily scheduler posts, no streaks, no timezone column.
The entire feature collapsed to: log who did what, report on it. Rationale: the
simpler scope delivers the social accountability value (leaderboard, nudges)
without any of the complexity of a scheduling system. Scheduling can be added
later in isolation.

**Deviations from the reduced spec:**

1. **`chores` table rebuilt via migration 002, not patched.** The Phase 0 schema
   had `recurrence_rule` and `assignee_strategy` on `chores`, and
   `assigned_to_user_id` / `completed_by_user_id` on `chore_completions`. Rather
   than `ALTER TABLE ADD COLUMN` + leaving dead columns, migration 002 drops and
   recreates both tables with the clean schema. Safe because no production data
   exists. Migration is guarded by checking for the old column so `init_db()` is
   idempotent.

2. **`init_db()` now runs two migrations in sequence.** Each is idempotent:
   001 uses `CREATE TABLE IF NOT EXISTS`; 002 is gated on the old column existing.
   No version table — guard logic is inline in Python.

3. **Imbalance nudge threshold is 20% of per-person average, not 20% of total.**
   A 20-1 split in a 2-person household flags the person with 1 (avg=10.5,
   threshold=2.1). A 60/40 split does not flag (4 > 2.1 = no nudge). This means
   only extreme imbalances surface — mild gaps are visible in the numbers without
   a warning attached.

4. **Test DB pattern established.** Existing tests were pure-function with no DB.
   The chore tests need DB access. Solution: patch `db.DB_PATH` directly with a
   `tmp_path` file — Python resolves module globals at call time, so `get_conn()`
   picks up the patched path without import reloads or monkeypatching the full
   function.

## 2026-05-13 — Phase 4 settle-up & polish

**Deviations:**

1. **settled_at filtering is in db.py, not splits.py.** The spec said "Modify balance
   compute in utils/splits.py to filter WHERE settled_at IS NULL." But
   `compute_balances` in splits.py takes pre-fetched Python lists — it can't SQL-filter.
   The filter is applied in `compute_household_balances` in db.py via SQL subqueries
   using `json_extract(payload_json, '$.purchase_id')`. splits.py is unchanged.

2. **settle_actions stamps only purchase_add and bill_add actions.** Chore logs,
   user_join, and supply_low_stock actions are not financial — settling them would
   silently wipe non-balance history. The WHERE clause limits to
   `action_type IN ('purchase_add', 'bill_add')`.

3. **Zero-balance /settle shows "all settled up" without offering /settle confirm.**
   If balances are already zero, there is no payment event to confirm. Offering confirm
   on a zero balance would reset the action baseline with nothing to show for it.

4. **PTB v21 retry: NetworkError and TimedOut only.** python-telegram-bot v21 handles
   RetryAfter (429 rate limit) internally. Our wrapper in utils/retry.py covers the
   remaining transient failures: NetworkError and TimedOut, 3x with 1s/2s/4s backoff.

5. **Backup dir is adjacent to DB_PATH, not hardcoded.** backups/ is created next to
   wherever DB_PATH points (default: repo root). This is safer than assuming CWD
   and works when DB_PATH is set to an absolute path via env var.

*Append new entries here as deviations occur.*
