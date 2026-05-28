"""Handlers for /start, /join, /setup, /undo, and /help.

ConversationHandler flows:
  /start  → LEADER_RENT_DAY → LEADER_CHORES [→ LEADER_CLARIFY] → END
  /setup  → LEADER_RENT_DAY → LEADER_CHORES [→ LEADER_CLARIFY] → END  (leader only)
  /join   → [MEMBER_NAME →] MEMBER_RENT → MEMBER_VENMO → END
            (skips to END immediately if all args supplied: /join Name Amount [@venmo])
"""
import json

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from db import get_conn, get_household, get_household_users, get_user
from llm import parse_chores

# --- Leader flow states ---
LEADER_RENT_DAY = 0
LEADER_CHORES = 1
LEADER_CLARIFY = 2

# --- Member flow states ---
MEMBER_NAME = 10
MEMBER_RENT = 11
MEMBER_VENMO = 12


# ── Leader flow ────────────────────────────────────────────────────────────────

async def _start_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.effective_user:
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    chat_title = update.effective_chat.title or "Our Household"

    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM households WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()

        if existing:
            await update.message.reply_text(
                "This household is already registered.\n"
                "Use /join to add yourself, or /setup to update rent and chores (leader only)."
            )
            return ConversationHandler.END

        household_id = conn.execute(
            "INSERT INTO households (telegram_chat_id, name, leader_telegram_user_id) "
            "VALUES (?, ?, ?)",
            (chat_id, chat_title, update.effective_user.id),
        ).lastrowid

    context.user_data["household_id"] = household_id
    context.user_data["is_setup"] = False

    await update.message.reply_text(
        f"Household *{chat_title}* created! Let's get you set up.\n\n"
        "What day of the month is rent due?\n"
        "_(Enter a number 1–28, e.g. 1 for the 1st)_",
        parse_mode="Markdown",
    )
    return LEADER_RENT_DAY


async def _setup_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.effective_user:
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    household = get_household(chat_id)
    if not household:
        await update.message.reply_text("Run /start first to register this group.")
        return ConversationHandler.END

    user = get_user(household["id"], update.effective_user.id)
    if not user:
        await update.message.reply_text("Join first with /join.")
        return ConversationHandler.END

    if not user["is_leader"]:
        await update.message.reply_text("Only the household leader can change setup.")
        return ConversationHandler.END

    context.user_data["household_id"] = household["id"]
    context.user_data["is_setup"] = True

    await update.message.reply_text(
        "What day of the month is rent due?\n"
        "_(Enter a number 1–28, e.g. 1 for the 1st)_",
        parse_mode="Markdown",
    )
    return LEADER_RENT_DAY


async def _leader_rent_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return LEADER_RENT_DAY

    text = update.message.text.strip()
    try:
        day = int(text)
        if not (1 <= day <= 28):
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Please enter a number between 1 and 28 (e.g. 1 for the 1st)."
        )
        return LEADER_RENT_DAY

    context.user_data["rent_due_day"] = day

    await update.message.reply_text(
        "What recurring chores should I track?\n\n"
        "Send a list like: `trash tue/fri, dishes daily, bathroom weekly`\n"
        "Natural language works too: `trash on tuesdays, someone does dishes every day`\n\n"
        "Or say *skip* to add chores later.",
        parse_mode="Markdown",
    )
    return LEADER_CHORES


async def _leader_chores(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return LEADER_CHORES

    text = update.message.text.strip()

    if text.lower() == "skip":
        await _finish_leader_setup(update, context, chore_names=[])
        return ConversationHandler.END

    chore_names, confident = await parse_chores(text)

    if confident and chore_names:
        await _finish_leader_setup(update, context, chore_names=chore_names)
        return ConversationHandler.END

    await update.message.reply_text(
        "I couldn't quite parse that chore list. Could you try again?\n"
        "Example: `trash, dishes, bathroom` — or say *skip* to add chores later.",
        parse_mode="Markdown",
    )
    return LEADER_CLARIFY


async def _leader_clarify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text.strip()

    if text.lower() == "skip":
        await _finish_leader_setup(update, context, chore_names=[])
        return ConversationHandler.END

    chore_names, confident = await parse_chores(text)
    extra = ""
    if not (confident and chore_names):
        chore_names = []
        extra = "\n_(Chores skipped — add them later with /did.)_"

    await _finish_leader_setup(update, context, chore_names=chore_names, extra=extra)
    return ConversationHandler.END


async def _finish_leader_setup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chore_names: list[str],
    extra: str = "",
) -> None:
    household_id = context.user_data.get("household_id")
    due_day = context.user_data.get("rent_due_day")
    if not household_id or not due_day or not update.message:
        return

    with get_conn() as conn:
        conn.execute(
            "UPDATE households SET rent_due_day = ? WHERE id = ?",
            (due_day, household_id),
        )
        for raw_name in chore_names:
            name = raw_name.strip().lower()
            if name:
                conn.execute(
                    "INSERT OR IGNORE INTO chores (household_id, name) VALUES (?, ?)",
                    (household_id, name),
                )

    day_str = f"{due_day}{_ordinal(due_day)}"
    chore_summary = f"Chores: {', '.join(n.strip().lower() for n in chore_names if n.strip())}" if chore_names else "No chores set yet."
    is_setup = context.user_data.get("is_setup", False)

    if is_setup:
        next_step = "Rent due date and chores have been updated."
    else:
        next_step = "Share this group with your roommates and have them run /join."

    await update.message.reply_text(
        f"*{'Setup updated' if is_setup else 'Setup complete'}!*\n\n"
        f"Rent due: the *{day_str}* of each month\n"
        f"{chore_summary}\n\n"
        f"{next_step}{extra}",
        parse_mode="Markdown",
    )


# ── Member flow (/join) ────────────────────────────────────────────────────────

def _parse_join_args(args: list[str]) -> tuple[str | None, int | None, str | None]:
    """Parse /join args → (name, rent_cents, venmo). rent_cents=None if not a positive number."""
    if not args:
        return None, None, None
    name = args[0]
    if len(args) >= 2:
        try:
            dollars = float(args[1].lstrip("$").replace(",", ""))
            if dollars > 0:
                venmo = args[2] if len(args) >= 3 else None
                return name, round(dollars * 100), venmo
        except ValueError:
            pass
    return name, None, None


async def _join_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.effective_chat or not update.message or not update.effective_user:
        return ConversationHandler.END

    chat_id = update.effective_chat.id
    household = get_household(chat_id)

    if not household:
        await update.message.reply_text(
            "Your leader needs to run /start first to set up the household."
        )
        return ConversationHandler.END

    tg_user_id = update.effective_user.id
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id FROM users WHERE household_id = ? AND telegram_user_id = ?",
            (household["id"], tg_user_id),
        ).fetchone()

    if existing:
        await update.message.reply_text("You're already in this household.")
        return ConversationHandler.END

    context.user_data["join_household_id"] = household["id"]
    context.user_data["join_is_leader"] = (
        household["leader_telegram_user_id"] == tg_user_id
    )

    args = context.args or []
    name, rent_cents, venmo = _parse_join_args(args)

    if name and rent_cents is not None:
        await _complete_join(update, context, name=name, rent_cents=rent_cents, venmo=venmo)
        return ConversationHandler.END

    if name:
        context.user_data["join_name"] = name
        await update.message.reply_text(
            f"Hi {name}! What's your monthly rent amount?\n_(Just the number, e.g. 900)_",
            parse_mode="Markdown",
        )
        return MEMBER_RENT

    await update.message.reply_text("What's your name?")
    return MEMBER_NAME


async def _member_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return MEMBER_NAME

    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Please enter your name.")
        return MEMBER_NAME

    context.user_data["join_name"] = name
    await update.message.reply_text(
        f"Hi {name}! What's your monthly rent amount?\n_(Just the number, e.g. 900)_",
        parse_mode="Markdown",
    )
    return MEMBER_RENT


async def _member_rent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return MEMBER_RENT

    text = update.message.text.strip().lstrip("$").replace(",", "")
    try:
        dollars = float(text)
        if dollars <= 0:
            raise ValueError
        rent_cents = round(dollars * 100)
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid rent amount (e.g. 900 or 1200.50)."
        )
        return MEMBER_RENT

    context.user_data["join_rent_cents"] = rent_cents
    await update.message.reply_text(
        "What's your Venmo handle? _(optional — send *skip* to skip)_",
        parse_mode="Markdown",
    )
    return MEMBER_VENMO


async def _member_venmo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not update.message or not update.message.text:
        return MEMBER_VENMO

    text = update.message.text.strip()
    venmo = None if text.lower() == "skip" else text

    name = context.user_data.get("join_name")
    rent_cents = context.user_data.get("join_rent_cents")
    if not name or rent_cents is None:
        await update.message.reply_text("Something went wrong. Please run /join again.")
        return ConversationHandler.END

    await _complete_join(update, context, name=name, rent_cents=rent_cents, venmo=venmo)
    return ConversationHandler.END


async def _complete_join(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    name: str,
    rent_cents: int,
    venmo: str | None,
) -> None:
    if not update.effective_user or not update.message:
        return

    household_id = context.user_data.get("join_household_id")
    is_leader = context.user_data.get("join_is_leader", False)
    if household_id is None:
        return

    tg_user_id = update.effective_user.id

    with get_conn() as conn:
        user_id = conn.execute(
            "INSERT INTO users "
            "(household_id, telegram_user_id, name, venmo_handle, rent_amount_cents, is_leader) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (household_id, tg_user_id, name, venmo, rent_cents, int(is_leader)),
        ).lastrowid

        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'user_join', ?)",
            (household_id, user_id, json.dumps({"user_id": user_id})),
        )

    dollars = rent_cents / 100
    venmo_note = f" (Venmo: {venmo})" if venmo else ""
    await update.message.reply_text(
        f"Welcome *{name}*{venmo_note}! You owe *${dollars:,.2f}/month*.\n\n"
        "Log a purchase anytime: `/bought dish soap $4`",
        parse_mode="Markdown",
    )


async def _cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message:
        await update.message.reply_text(
            "Cancelled. Run /start or /join again anytime."
        )
    return ConversationHandler.END


# ── ConversationHandler factories (called from bot.py) ─────────────────────────

def make_start_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("start", _start_entry),
            CommandHandler("setup", _setup_entry),
        ],
        states={
            LEADER_RENT_DAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, _leader_rent_day)],
            LEADER_CHORES: [MessageHandler(filters.TEXT & ~filters.COMMAND, _leader_chores)],
            LEADER_CLARIFY: [MessageHandler(filters.TEXT & ~filters.COMMAND, _leader_clarify)],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        name="leader_setup",
        persistent=False,
    )


def make_join_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("join", _join_entry)],
        states={
            MEMBER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, _member_name)],
            MEMBER_RENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, _member_rent)],
            MEMBER_VENMO: [MessageHandler(filters.TEXT & ~filters.COMMAND, _member_venmo)],
        },
        fallbacks=[CommandHandler("cancel", _cancel)],
        name="member_join",
        persistent=False,
    )


# ── /undo ──────────────────────────────────────────────────────────────────────

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
            await update.message.reply_text(f"Can't automatically undo '{action_type}'.")
            return

        conn.execute(
            "UPDATE actions SET reversed_at = datetime('now') WHERE id = ?",
            (action["id"],),
        )

    await update.message.reply_text(f"Undone: {description}.")


# ── /help ──────────────────────────────────────────────────────────────────────

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — show available commands."""
    if not update.message:
        return
    await update.message.reply_text(
        "*Roomagent commands*\n\n"
        "/start — register this group as a household (leader)\n"
        "/setup — update rent due date or chores (leader only)\n"
        "/join — add yourself as a roommate\n"
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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
