"""Handler for /settle and /settle confirm."""
import urllib.parse

from telegram import Update
from telegram.ext import ContextTypes

from db import (
    compute_household_balances,
    get_household,
    get_household_users,
    get_user,
    settle_actions,
)
from utils.retry import send_with_retry
from utils.splits import simplify_debts


async def settle_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/settle [confirm] — show settle-up summary, or confirm and reset balances."""
    if not update.effective_chat or not update.message or not update.effective_user:
        return

    household = get_household(update.effective_chat.id)
    if not household:
        await update.message.reply_text("Run /start first to register this group.")
        return

    user = get_user(household["id"], update.effective_user.id)
    if not user:
        await update.message.reply_text("Join first with `/join YourName`.", parse_mode="Markdown")
        return

    if context.args and context.args[0].lower() == "confirm":
        await _confirm(update, household)
    else:
        await _show(update, household)


async def _show(update: Update, household: object) -> None:
    """Compute and post the minimum payment list to the group."""
    balances = compute_household_balances(household["id"])
    if not balances:
        await update.message.reply_text("No roommates in this household yet.")
        return

    users = {u["id"]: u for u in get_household_users(household["id"])}
    debts = simplify_debts(balances)

    if not debts:
        await send_with_retry(
            lambda: update.message.reply_text("All settled up — nothing to pay! 🎉")
        )
        return

    lines = ["*Settle-up*\n"]
    for payer_id, payee_id, amount_cents in debts:
        payer_name = users[payer_id]["name"] if payer_id in users else f"user#{payer_id}"
        payee_row = users.get(payee_id)
        payee_name = payee_row["name"] if payee_row else f"user#{payee_id}"
        dollars = amount_cents / 100

        venmo = payee_row["venmo_handle"] if payee_row else None
        if venmo:
            link = _venmo_link(venmo, amount_cents)
            line = f"*{payer_name}* → *{payee_name}*: ${dollars:.2f}  [Pay on Venmo]({link})"
        else:
            line = f"*{payer_name}* → *{payee_name}*: ${dollars:.2f}"

        lines.append(line)

    lines.append("\nOnce everyone has paid, run `/settle confirm` to reset balances.")

    await send_with_retry(
        lambda: update.message.reply_text(
            "\n".join(lines),
            parse_mode="Markdown",
            disable_web_page_preview=True,
        )
    )


async def _confirm(update: Update, household: object) -> None:
    """Mark all financial actions as settled and announce the reset."""
    count = settle_actions(household["id"])
    if count == 0:
        await update.message.reply_text(
            "Nothing to settle — no unsettled purchases or bills on record."
        )
        return

    await send_with_retry(
        lambda: update.message.reply_text(
            f"Settled. {count} transaction{'s' if count != 1 else ''} marked — "
            "balances reset. Next cycle starts fresh."
        )
    )


def _venmo_link(handle: str, amount_cents: int) -> str:
    """Build a Venmo deep link. handle may or may not have a leading '@'."""
    clean = handle.lstrip("@")
    dollars = amount_cents / 100
    note = urllib.parse.quote("Roomagent settle-up")
    return f"venmo://paycharge?txn=pay&recipients={clean}&amount={dollars:.2f}&note={note}"
