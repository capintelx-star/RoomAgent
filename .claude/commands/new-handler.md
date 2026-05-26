# /new-handler — scaffold a new handler end-to-end

Scaffold a complete new slash command handler for roomagent. Follow every step in order.

## What to ask the user first (if not already specified)

- Command name (e.g. `remind`)
- What it does in one sentence
- Does it mutate data? (determines whether action log is needed)
- Does it need Claude / LLM parsing? (most commands don't)

---

## Step 1 — Create the handler function

Create `src/handlers/<name>.py` (or add to an existing file if thematically related).

Follow this exact pattern — every handler is `async def`, returns `None`, and guards against missing update fields:

```python
"""Handlers for /<name>."""
from telegram import Update
from telegram.ext import ContextTypes

from db import get_conn, get_household, get_user


async def <name>_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/<name> — one-line description."""
    if not update.effective_chat or not update.message or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    household = get_household(chat_id)
    if not household:
        await update.message.reply_text("Run /start first to register this group.")
        return

    user = get_user(household["id"], update.effective_user.id)
    if not user:
        await update.message.reply_text("Join first with `/join YourName`.", parse_mode="Markdown")
        return

    # --- handler logic here ---
```

**PTB v21 rules:**
- `async def` always — never sync
- `await` every `.reply_text()`, `.send_message()`, and `send_with_retry()` call
- Never create your own event loop; the bot's loop owns execution

**If the handler sends to a DM**, wrap in try/except for `Forbidden`:
```python
from telegram.error import Forbidden
try:
    await context.bot.send_message(chat_id=user["telegram_user_id"], text=msg)
except Forbidden:
    await update.message.reply_text("(couldn't DM you — message the bot first)\n\n" + msg)
```

---

## Step 2 — Add action log entry (mutating handlers only)

If the handler writes to `purchases`, `bills`, `bill_shares`, `chore_completions`, or `supplies`, it **must** insert into `actions` within the same `get_conn()` context manager:

```python
import json

with get_conn() as conn:
    row_id = conn.execute(
        "INSERT INTO <table> (...) VALUES (...)", (...)
    ).lastrowid

    conn.execute(
        "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
        "VALUES (?, ?, '<action_type>', ?)",
        (household["id"], user["id"], json.dumps({"<table>_id": row_id})),
    )
```

Add an `/undo` branch in `src/handlers/onboarding.py` → `undo_cmd()` so the new action type is reversible. Match the existing `if action_type == "..."` pattern.

---

## Step 3 — Register in `bot.py`

Add the import and `add_handler` call. Keep the import block grouped by module:

```python
# in the import block at top of src/bot.py:
from handlers.<name> import <name>_cmd

# in main(), with the other CommandHandlers:
app.add_handler(CommandHandler("<name>", <name>_cmd))
```

---

## Step 4 — Update `/help`

In `src/handlers/onboarding.py` → `help_cmd()`, add a line to the reply text:

```
/<name> — short description\n
```

Keep alphabetical order within the thematic group (onboarding / supply / bills / chores / settle / meta).

---

## Step 5 — Write a test

Create or extend a test file in `tests/`. If the handler touches the DB, use the `fresh_db` fixture pattern from `test_chores.py`:

```python
import db as db_module
from db import get_conn, init_db
import pytest

@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    test_db = str(tmp_path / "test.db")
    original = db_module.DB_PATH
    db_module.DB_PATH = test_db
    init_db()
    yield
    db_module.DB_PATH = original
```

Write tests for:
1. Happy path — handler does the right thing when household + user exist
2. Guard paths — missing household, missing user (assert the error reply is sent or the function returns early)
3. Action log written (if mutating) — query `actions` table after call and assert the row exists with the right `action_type`
4. `/undo` reversal (if action log was added) — verify the undo branch deletes/restores correctly

**Test async handlers** by calling the internal helper functions (e.g. `_log_chore`, `_save_purchase`) directly where possible — these are sync-friendly and don't require mocking Telegram. For full handler tests, mock `update` and `context` objects.

---

## Step 6 — Run tests

```bash
pytest tests/ -v
```

All 14 existing tests must still pass. Fix any regressions before declaring done.
