"""Handlers for /bought, /need, and free-text purchase/low-stock routing."""
import json

from telegram import Update
from telegram.ext import ContextTypes

from db import get_conn, get_household, get_user
from llm import IntentResult, parse_message
from utils.amazon import affiliate_search_link
from utils.prefilter import should_invoke_llm


async def bought_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bought <text> — log a purchase, Claude parses the text."""
    if not update.effective_chat or not update.message or not update.effective_user:
        return

    household, user = await _require_household_and_user(update)
    if not household or not user:
        return

    text = " ".join(context.args or []).strip()
    if not text:
        await update.message.reply_text(
            "What did you buy?\nExample: `/bought TP $12 Costco`",
            parse_mode="Markdown",
        )
        return

    result = parse_message(text)
    if result.intent != "purchase":
        # /bought was explicit, so try parsing the whole thing as a purchase
        # by nudging the intent if amount_cents was found
        if result.amount_cents:
            result = IntentResult(
                intent="purchase",
                items=result.items or text,
                amount_cents=result.amount_cents,
                note=result.note,
                category=result.category,
            )
        else:
            await update.message.reply_text(
                "I need an amount. Try: `/bought dish soap $4.99`",
                parse_mode="Markdown",
            )
            return

    await _save_purchase(update, household, user, result)


async def need_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/need <item> — flag low stock and return an Amazon affiliate link."""
    if not update.effective_chat or not update.message or not update.effective_user:
        return

    household, user = await _require_household_and_user(update)
    if not household or not user:
        return

    item = " ".join(context.args or []).strip()
    if not item:
        await update.message.reply_text(
            "What do you need?\nExample: `/need dish soap`",
            parse_mode="Markdown",
        )
        return

    await _flag_low_stock(update, household, user, item)


async def free_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle non-command messages:
    1. Run regex prefilter — if no purchase/bill keywords, silently ignore.
    2. Check household + user registration — silently ignore if not set up.
    3. Call Claude to classify intent.
    4. Route to the right action.
    """
    if not update.effective_chat or not update.message or not update.message.text:
        return

    text = update.message.text
    bot_username = context.bot.username if context.bot else None

    # Is this a reply to one of our bot's messages?
    reply_msg = update.message.reply_to_message
    is_reply_to_bot = bool(
        reply_msg
        and reply_msg.from_user
        and bot_username
        and reply_msg.from_user.username == bot_username
    )

    if not should_invoke_llm(text, bot_username=bot_username, is_reply_to_bot=is_reply_to_bot):
        return

    chat_id = update.effective_chat.id
    household = get_household(chat_id)
    if not household or not update.effective_user:
        return

    user = get_user(household["id"], update.effective_user.id)
    if not user:
        return  # not joined — silently ignore free text

    result = parse_message(text)

    if result.intent == "purchase":
        if not result.amount_cents:
            await update.message.reply_text(
                "Looks like a purchase — but I didn't catch the amount.\n"
                "Try: `/bought TP $12`",
                parse_mode="Markdown",
            )
            return
        await _save_purchase(update, household, user, result)

    elif result.intent == "low_stock":
        item = result.item or text.strip()
        await _flag_low_stock(update, household, user, item)

    elif result.intent == "query_balance":
        await update.message.reply_text("Use /owe to see the balance breakdown (sent via DM).")

    elif result.intent == "bill_log":
        from handlers.bills import handle_bill_log
        await handle_bill_log(update, household, user, result)

    elif result.intent == "chore_done":
        if result.chore_name:
            from handlers.chores import handle_chore_done
            await handle_chore_done(update, household, user, result.chore_name)
        # no chore_name → Claude wasn't sure what chore; silently ignore

    # ignore / unknown → no response


# --- Shared helpers ---

async def _require_household_and_user(update: Update):
    """
    Check that the chat has a registered household and the sender has joined.
    Sends an appropriate error reply if not. Returns (household, user) or (None, None).
    """
    chat_id = update.effective_chat.id
    household = get_household(chat_id)
    if not household:
        await update.message.reply_text("Run /start first to register this group.")
        return None, None

    user = get_user(household["id"], update.effective_user.id)
    if not user:
        await update.message.reply_text("Join first with `/join YourName`.", parse_mode="Markdown")
        return None, None

    return household, user


async def _save_purchase(update: Update, household, user, result: IntentResult) -> None:
    """Insert a purchase row, upsert the supply record, log the action, and reply."""
    items_str = result.items or "items"
    amount = result.amount_cents  # already validated non-None by caller

    with get_conn() as conn:
        purchase_id = conn.execute(
            "INSERT INTO purchases "
            "(household_id, buyer_id, item, category, amount_cents, note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (household["id"], user["id"], items_str, result.category, amount, result.note),
        ).lastrowid

        # Keep supply record up to date: reset low_flag, update last purchased time.
        conn.execute(
            "INSERT INTO supplies (household_id, name, last_purchased_at, low_flag) "
            "VALUES (?, ?, datetime('now'), 0) "
            "ON CONFLICT(household_id, name) DO UPDATE SET "
            "last_purchased_at = datetime('now'), low_flag = 0",
            (household["id"], items_str),
        )

        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'purchase_add', ?)",
            (household["id"], user["id"], json.dumps({"purchase_id": purchase_id})),
        )

    dollars = amount / 100
    note_str = f" — {result.note}" if result.note else ""
    await update.message.reply_text(
        f"Logged: *{items_str}*{note_str} — ${dollars:.2f}\n"
        f"Buyer: {user['name']}",
        parse_mode="Markdown",
    )


async def _flag_low_stock(update: Update, household, user, item: str) -> None:
    """Mark an item as low stock, log the action, and reply with an Amazon link."""
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT id, low_flag FROM supplies WHERE household_id = ? AND name = ?",
            (household["id"], item),
        ).fetchone()

        if existing:
            previous_flag = existing["low_flag"]
            conn.execute("UPDATE supplies SET low_flag = 1 WHERE id = ?", (existing["id"],))
            supply_id = existing["id"]
        else:
            supply_id = conn.execute(
                "INSERT INTO supplies (household_id, name, low_flag) VALUES (?, ?, 1)",
                (household["id"], item),
            ).lastrowid
            previous_flag = 0

        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'supply_low_stock', ?)",
            (
                household["id"],
                user["id"],
                json.dumps({"supply_id": supply_id, "previous_low_flag": previous_flag}),
            ),
        )

    link = affiliate_search_link(item)
    await update.message.reply_text(
        f"Flagged *{item}* as low stock.\n\nShop on Amazon: {link}",
        parse_mode="Markdown",
    )
