# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Roomagent is a Telegram bot for roommate supply/bill/chore/settle tracking. SQLite + python-telegram-bot v21 + Anthropic Claude API for NL intent parsing.

## Commands

```bash
pip install -e ".[dev]"   # first-time setup (installs roomagent entry point + dev deps)
roomagent                 # start the bot (requires .env)
python -m pytest                    # run all tests (use python -m pytest, not bare pytest — resolves to wrong env on this machine)
python -m pytest tests/test_chores.py                        # run one file
python -m pytest tests/test_chores.py::test_log_chore_inserts_completion  # run one test
```

`pyproject.toml` sets `asyncio_mode = "auto"` and `pythonpath = ["src"]`, so tests resolve `src/` imports and async test functions need no decorators.

## Architecture

**Entry point:** `src/bot.py` — registers all `CommandHandler`s and the single free-text `MessageHandler`, then calls `app.run_polling()`. The APScheduler is started in `_post_init` (after the event loop is up) and stopped in `_post_shutdown`.

**Free-text flow:** `free_text_handler` in `supplies.py` → `prefilter.py` (regex gate, avoids LLM cost on chitchat) → `llm.py` (Claude tool-use → `IntentResult`) → domain handler.

**Database:** `src/db.py` owns the connection factory (`get_conn()`), all migrations (`init_db()`), shared lookup helpers (`get_household`, `get_user`, `get_household_users`), and the balance computation (`compute_household_balances`). Migrations have no version table — each is guarded inline with `_column_exists()`.

**Balances:** `compute_household_balances(household_id)` in `db.py` is authoritative. It SQL-filters settled records via `json_extract(payload_json, '$.purchase_id')`. `compute_balances()` in `utils/splits.py` is a pure-Python helper that takes pre-fetched lists and does **not** filter settled records — do not use it for display.

**Scheduler:** `src/scheduler.py` uses APScheduler v3 (pinned `<4` — v4 API changed entirely). Callbacks that send Telegram messages must use `asyncio.run_coroutine_threadsafe(coro, bot._loop)` — the scheduler runs in a background thread, not the bot's event loop.

## Critical rules

**PTB v21 async:** All handlers are `async def`. `await` every Telegram API call. Never call `asyncio.run()` inside a handler. PTB v21 handles `RetryAfter` (429) internally — `utils/retry.py` wraps only `NetworkError` and `TimedOut` (3× backoff: 1s/2s/4s).

**Action log:** Every handler that writes to `purchases`, `bills`/`bill_shares`, `chore_completions`, or `supplies` must also insert into `actions` within the same `get_conn()` context manager. The `payload_json` must contain enough to reverse the write in `/undo`.

| action_type | payload_json keys |
|---|---|
| `purchase_add` | `purchase_id` |
| `bill_add` | `bill_id` |
| `chore_done` | `completion_id` |
| `supply_low_stock` | `supply_id`, `previous_low_flag` |
| `user_join` | `user_id` |

`settle_actions()` stamps only `purchase_add` and `bill_add` — chore and supply actions are not financial.

**Migrations:** Add a numbered SQL file under `migrations/`, then add an idempotent guard in `init_db()` in `db.py`. Guard on column presence (`_column_exists`) — not a version counter.

**Test DB pattern:** Patch `db_module.DB_PATH` directly — `get_conn()` resolves it as a module global at call time, so no import reload is needed.

```python
import db as db_module
from db import get_conn, init_db

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    original = db_module.DB_PATH
    db_module.DB_PATH = str(tmp_path / "test.db")
    init_db()
    yield
    db_module.DB_PATH = original
```

**DM fallback:** Wrap `context.bot.send_message` in `try/except Forbidden` and fall back to a group reply — Telegram blocks bots from initiating DMs to users who haven't messaged first.

## Known deferred features (do not implement without discussion)

- Custom rent splits (`rent_share_pct`, `split_method` columns exist but are unused — all splits are equal)
- Rent reminder month-boundary handling (`due_day ≤ 3` skipped for now)
- Chore recurrence / round-robin assignment (Phase 3 was scoped to tracking-only)

## Custom commands

- `/new-handler` — scaffold a new handler end-to-end (async def, action log, bot.py registration, /help update, tests)
- `/run` — run pytest; start the bot only if all tests pass
