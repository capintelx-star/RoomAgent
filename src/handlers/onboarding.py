"""Handlers for /start, /join, /undo, and /help."""
import json

from telegram import Update
from telegram.ext import ContextTypes

from db import get_conn, get_household, get_user


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — register this group chat as a household."""
    if not update.effective_chat or not update.message:
        return

    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Our Household"

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM households WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()

        if existing:
            await update.message.reply_text(
                "This household is already registered. Use /join to add yourself."
            )
            return

        conn.execute(
            "INSERT INTO households (telegram_chat_id, name) VALUES (?, ?)",
            (chat_id, chat_title),
        )

    await update.message.reply_text(
        f"Household *{chat_title}* registered!\n\n"
        "Each roommate should now run:\n"
        "`/join YourName` — or `/join YourName @venmo_handle`\n\n"
        "Once everyone's in, try:\n"
        "`/bought TP $12 Costco` — log a purchase",
        parse_mode="Markdown",
    )


async def join_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/join <name> [venmo_handle] — add yourself as a roommate."""
    if not update.effective_chat or not update.message or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    household = get_household(chat_id)
    if not household:
        await update.message.reply_text("Run /start first to register this group.")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: `/join YourName` or `/join YourName @venmo_handle`",
            parse_mode="Markdown",
        )
        return

    name = args[0]
    venmo = args[1] if len(args) > 1 else None

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE household_id = ? AND telegram_user_id = ?",
            (household["id"], update.effective_user.id),
        ).fetchone()

        if existing:
            await update.message.reply_text("You're already in this household.")
            return

        user_id = conn.execute(
            "INSERT INTO users (household_id, telegram_user_id, name, venmo_handle) "
            "VALUES (?, ?, ?, ?)",
            (household["id"], update.effective_user.id, name, venmo),
        ).lastrowid

        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'user_join', ?)",
            (household["id"], user_id, json.dumps({"user_id": user_id})),
        )

    venmo_str = f" (Venmo: {venmo})" if venmo else ""
    await update.message.reply_text(
        f"Welcome, *{name}*{venmo_str}!\n\n"
        "Log a purchase anytime: `/bought dish soap $4`",
        parse_mode="Markdown",
    )


async def undo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/undo — reverse the calling user's last action."""
    if not update.effective_chat or not update.message or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    household = get_household(chat_id)
    if not household:
        await update.message.reply_text("This household isn't registered yet.")
        return

    user = get_user(household["id"], update.effective_user.id)
    if not user:
        await update.message.reply_text("You haven't joined yet. Use /join.")
        return

    with get_conn() as conn:
        action = conn.execute(
            "SELECT * FROM actions "
            "WHERE household_id = ? AND user_id = ? AND reversed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (household["id"], user["id"]),
        ).fetchone()

        if not action:
            await update.message.reply_text("Nothing to undo.")
            return

        payload = json.loads(action["payload_json"])
        action_type = action["action_type"]
        description = ""

        if action_type == "purchase_add":
            purchase_id = payload.get("purchase_id")
            row = conn.execute(
                "SELECT item, amount_cents FROM purchases WHERE id = ?", (purchase_id,)
            ).fetchone()
            if row:
                description = f"{row['item']} (${row['amount_cents'] / 100:.2f})"
                conn.execute("DELETE FROM purchases WHERE id = ?", (purchase_id,))
            else:
                await update.message.reply_text("That purchase no longer exists.")
                return

        elif action_type == "supply_low_stock":
            supply_id = payload.get("supply_id")
            prev_flag = payload.get("previous_low_flag", 0)
            row = conn.execute("SELECT name FROM supplies WHERE id = ?", (supply_id,)).fetchone()
            if row:
                description = f"low-stock flag on {row['name']}"
                conn.execute(
                    "UPDATE supplies SET low_flag = ? WHERE id = ?", (prev_flag, supply_id)
                )
            else:
                await update.message.reply_text("That supply record no longer exists.")
                return

        elif action_type == "bill_add":
            bill_id = payload.get("bill_id")
            row = conn.execute("SELECT type, amount_cents FROM bills WHERE id = ?", (bill_id,)).fetchone()
            if row:
                description = f"{row['type']} bill (${row['amount_cents'] / 100:.2f})"
                conn.execute("DELETE FROM bill_shares WHERE bill_id = ?", (bill_id,))
                conn.execute("DELETE FROM bills WHERE id = ?", (bill_id,))
            else:
                await update.message.reply_text("That bill no longer exists.")
                return

        elif action_type == "chore_done":
            completion_id = payload.get("completion_id")
            row = conn.execute(
                "SELECT c.name AS chore_name "
                "FROM chore_completions cc "
                "JOIN chores c ON c.id = cc.chore_id "
                "WHERE cc.id = ?",
                (completion_id,),
            ).fetchone()
            if row:
                description = f"chore: {row['chore_name']}"
                conn.execute("DELETE FROM chore_completions WHERE id = ?", (completion_id,))
            else:
                await update.message.reply_text("That chore log no longer exists.")
                return

        else:
            await update.message.reply_text(
                f"Can't automatically undo '{action_type}'."
            )
            return

        conn.execute(
            "UPDATE actions SET reversed_at = datetime('now') WHERE id = ?",
            (action["id"],),
        )

    await update.message.reply_text(f"Undone: {description}.")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — show available commands."""
    if not update.message:
        return
    await update.message.reply_text(
        "*Roomagent commands*\n\n"
        "/start — register this group as a household\n"
        "/join Name [@venmo] — add yourself as a roommate\n"
        "/bought <text> — log a purchase  e.g. `/bought TP $12 Costco`\n"
        "/need <item> — flag low stock + Amazon link\n"
        "/rent — this month's rent status\n"
        "/owe — DMs you the current balance\n"
        "/did <chore> — log a chore  e.g. `/did dishes`\n"
        "/chore <name> — same as /did  e.g. `/chore dishes`\n"
        "/chorestats — DMs you the monthly chore leaderboard\n"
        "/chorestats all — all-time leaderboard\n"
        "/settle — show who owes whom (Venmo links included)\n"
        "/settle confirm — mark everything paid, reset balances\n"
        "/undo — reverse your last action\n"
        "/help — this message\n\n"
        "Free text works too:\n"
        "`got dish soap $18` · `I cleaned the bathroom`",
        parse_mode="Markdown",
    )
