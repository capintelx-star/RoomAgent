"""
Handlers for /rent, /owe, and free-text utility bill logging.

Rent lives in recurring_bills (is_rent=1) for configuration and reminders.
One-off bills (utilities) go into bills + bill_shares and affect balances.

Balance computation:
  - All-time, computed on demand from purchases + unpaid bill_shares.
  - No stored balance column anywhere.
  - /settle (Phase 4) will mark actions as settled to reset the running total.
"""
import json
from datetime import date

import telegram
from telegram import Update
from telegram.ext import ContextTypes

from db import (
    compute_household_balances,
    get_conn,
    get_household,
    get_household_users,
    get_user,
)
from llm import IntentResult
from utils.splits import simplify_debts


async def rent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /rent              → show rent config + next due date
    /rent <amount> <due_day> → set up or update rent (e.g. /rent 2400 1)
    """
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

    args = context.args or []

    if args:
        # Setup / update rent
        try:
            amount_dollars = float(args[0].lstrip("$").replace(",", ""))
            amount_cents = round(amount_dollars * 100)
            due_day = int(args[1])
            if not (1 <= due_day <= 28):
                raise ValueError("due_day must be 1–28")
        except (IndexError, ValueError):
            await update.message.reply_text(
                "Usage: `/rent <amount> <due_day>`\n"
                "Example: `/rent 2400 1` sets $2400/month due on the 1st.",
                parse_mode="Markdown",
            )
            return

        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id FROM recurring_bills WHERE household_id = ? AND is_rent = 1",
                (household["id"],),
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE recurring_bills SET amount_cents = ?, due_day = ? WHERE id = ?",
                    (amount_cents, due_day, existing["id"]),
                )
                msg = "updated"
            else:
                conn.execute(
                    "INSERT INTO recurring_bills "
                    "(household_id, name, amount_cents, due_day, split_method, is_rent) "
                    "VALUES (?, 'Rent', ?, ?, 'equal', 1)",
                    (household["id"], amount_cents, due_day),
                )
                msg = "set up"

        users = get_household_users(household["id"])
        n = len(users) or 1
        per_person = amount_cents / n / 100
        await update.message.reply_text(
            f"Rent {msg}: *${amount_dollars:,.2f}/month*, due on the *{due_day}{_ordinal(due_day)}*.\n"
            f"Split equally: *${per_person:,.2f}/person* ({n} roommates).",
            parse_mode="Markdown",
        )

    else:
        # Show status
        with get_conn() as conn:
            rent = conn.execute(
                "SELECT amount_cents, due_day FROM recurring_bills "
                "WHERE household_id = ? AND is_rent = 1",
                (household["id"],),
            ).fetchone()

        if not rent:
            await update.message.reply_text(
                "Rent isn't configured yet.\n"
                "Set it up: `/rent 2400 1` (amount, due day of month)",
                parse_mode="Markdown",
            )
            return

        today = date.today()
        due_day = rent["due_day"]
        days_until = due_day - today.day
        amount = rent["amount_cents"] / 100

        if days_until > 0:
            timing = f"due in {days_until} day{'s' if days_until != 1 else ''}"
        elif days_until == 0:
            timing = "due *today*"
        else:
            timing = f"was due {-days_until} day{'s' if days_until != -1 else ''} ago"

        users = get_household_users(household["id"])
        n = len(users) or 1
        per_person = amount / n

        await update.message.reply_text(
            f"*Rent status*\n\n"
            f"${amount:,.2f}/month — {timing} (the {due_day}{_ordinal(due_day)})\n"
            f"Per person: ${per_person:,.2f} ({n} roommates, equal split)\n\n"
            "Log rent paid: `paid rent $2400`",
            parse_mode="Markdown",
        )


async def owe_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /owe — DM the caller a full balance breakdown.

    If the bot can't DM (user hasn't started a conversation with the bot yet),
    fall back to a group message asking them to DM first.
    """
    if not update.effective_chat or not update.message or not update.effective_user:
        return

    chat_id = update.effective_chat.id
    household = get_household(chat_id)
    if not household:
        await update.message.reply_text("This group isn't registered yet. Run /start.")
        return

    user = get_user(household["id"], update.effective_user.id)
    if not user:
        await update.message.reply_text("Join first with `/join YourName`.", parse_mode="Markdown")
        return

    balances = compute_household_balances(household["id"])
    if not balances:
        await update.message.reply_text("No roommates in this household yet.")
        return

    users = {u["id"]: u["name"] for u in get_household_users(household["id"])}
    my_balance = balances.get(user["id"], 0)

    # Build balance message
    lines = ["*Roomagent balance summary*", "_(all-time)_", ""]

    # Everyone's standing
    for uid, cents in sorted(balances.items(), key=lambda x: -x[1]):
        name = users.get(uid, "?")
        marker = " ← you" if uid == user["id"] else ""
        sign = "+" if cents >= 0 else ""
        lines.append(f"{name}: {sign}${cents / 100:,.2f}{marker}")

    lines.append("")

    # Simplified transactions
    debts = simplify_debts(balances)
    if debts:
        lines.append("*To settle up:*")
        for payer_id, payee_id, amount in debts:
            payer = users.get(payer_id, "?")
            payee = users.get(payee_id, "?")
            lines.append(f"• {payer} → {payee}: ${amount / 100:,.2f}")
    else:
        lines.append("Everyone is square!")

    if my_balance > 0:
        lines.append(f"\nYou are *owed ${my_balance / 100:,.2f}* overall.")
    elif my_balance < 0:
        lines.append(f"\nYou *owe ${-my_balance / 100:,.2f}* overall.")
    else:
        lines.append("\nYou're all square.")

    message_text = "\n".join(lines)

    # Try to DM; fall back to group message with instructions if the bot can't
    try:
        await context.bot.send_message(
            chat_id=update.effective_user.id,
            text=message_text,
            parse_mode="Markdown",
        )
        await update.message.reply_text("Sent you a DM with the balance breakdown.")
    except telegram.error.Forbidden:
        tg_username = update.effective_user.username
        mention = f"@{tg_username}" if tg_username else user["name"]
        await update.message.reply_text(
            f"Hey {mention}, I can't DM you yet — Telegram requires you to message me first.\n\n"
            "Start a chat with me directly, then run /owe again and I'll send it privately."
        )


async def handle_bill_log(
    update: Update,
    household,
    user,
    result: IntentResult,
) -> None:
    """
    Save a utility bill, auto-split it equally, log the action, and reply.
    Called from supplies.free_text_handler when intent == 'bill_log'.
    """
    if not result.amount_cents:
        await update.message.reply_text(
            "Looks like a bill — but I didn't catch the amount.\n"
            "Try: `paid electric $87`",
            parse_mode="Markdown",
        )
        return

    bill_type = result.bill_type or "utility"
    amount = result.amount_cents
    users = get_household_users(household["id"])
    n = len(users)

    if n == 0:
        await update.message.reply_text("No roommates in this household yet.")
        return

    per_person = amount // n  # integer cents; payer absorbs rounding

    with get_conn() as conn:
        bill_id = conn.execute(
            "INSERT INTO bills "
            "(household_id, type, amount_cents, paid_by_user_id, split_method) "
            "VALUES (?, ?, ?, ?, 'equal')",
            (household["id"], bill_type, amount, user["id"]),
        ).lastrowid

        # Create one bill_share per roommate who isn't the payer
        for u in users:
            if u["id"] != user["id"]:
                conn.execute(
                    "INSERT INTO bill_shares (bill_id, user_id, amount_cents, paid) "
                    "VALUES (?, ?, ?, 0)",
                    (bill_id, u["id"], per_person),
                )

        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'bill_add', ?)",
            (household["id"], user["id"], json.dumps({"bill_id": bill_id})),
        )

    # Reply with split breakdown
    dollars = amount / 100
    share_dollars = per_person / 100
    others = [u["name"] for u in users if u["id"] != user["id"]]
    split_line = ", ".join(others) if others else "nobody else yet"

    await update.message.reply_text(
        f"Logged: *{bill_type}* bill — ${dollars:.2f}\n"
        f"Paid by: {user['name']}\n"
        f"Each of [{split_line}] owes *${share_dollars:.2f}*\n\n"
        "Run /owe to see the full balance.",
        parse_mode="Markdown",
    )


# --- Helpers ---

def _ordinal(n: int) -> str:
    """Return the ordinal suffix for a number: 1 → 'st', 2 → 'nd', etc."""
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
