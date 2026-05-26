"""Handlers for /did and /chorestats."""
import json
from collections import defaultdict
from datetime import date

import telegram
from telegram import Update
from telegram.ext import ContextTypes

from db import get_conn, get_household, get_household_users, get_user


async def did_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/did <chore> — log that the caller just did a chore."""
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

    raw = " ".join(context.args or []).strip()
    if not raw:
        await update.message.reply_text(
            "What did you do?\nExample: `/did dishes`",
            parse_mode="Markdown",
        )
        return

    chore_name = _normalize_chore_name(raw)
    _log_chore(household["id"], user["id"], chore_name)
    await update.message.reply_text(
        f"Logged: {user['name']} did *{chore_name}*. Nice work!",
        parse_mode="Markdown",
    )


async def chorestats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chorestats [all] — DMs the caller a monthly or all-time leaderboard."""
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

    all_time = bool(context.args and context.args[0].lower() == "all")
    report = _build_stats_report(household["id"], all_time=all_time)

    try:
        await update.effective_user.send_message(report, parse_mode="Markdown")
        if update.effective_chat.type != "private":
            await update.message.reply_text("Chore stats sent to your DMs.")
    except telegram.error.Forbidden:
        await update.message.reply_text(report, parse_mode="Markdown")


async def chore_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/chore <name> — mark a chore as done."""
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

    raw = " ".join(context.args or []).strip()
    if not raw:
        await update.message.reply_text(
            "What chore did you do?\nExample: `/chore dishes`",
            parse_mode="Markdown",
        )
        return

    await handle_chore_done(update, household, user, raw)


async def handle_chore_done(
    update: Update, household: object, user: object, chore_name: str
) -> None:
    """Called from free_text_handler when Claude detects a chore_done intent."""
    name = _normalize_chore_name(chore_name)
    _log_chore(household["id"], user["id"], name)
    await update.message.reply_text(
        f"Logged: {user['name']} did *{name}*. Nice work!",
        parse_mode="Markdown",
    )


# --- Internal helpers ---

def _normalize_chore_name(raw: str) -> str:
    """Lowercase, strip, and remove leading 'the' for natural phrasing."""
    name = raw.strip().lower()
    if name.startswith("the "):
        name = name[4:]
    return name


def _log_chore(household_id: int, user_id: int, chore_name: str) -> int:
    """Upsert the chore record, insert a completion, and write to the action log."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO chores (household_id, name) VALUES (?, ?)",
            (household_id, chore_name),
        )
        chore = conn.execute(
            "SELECT id FROM chores WHERE household_id = ? AND name = ?",
            (household_id, chore_name),
        ).fetchone()

        completion_id = conn.execute(
            "INSERT INTO chore_completions (chore_id, user_id) VALUES (?, ?)",
            (chore["id"], user_id),
        ).lastrowid

        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'chore_done', ?)",
            (household_id, user_id, json.dumps({"completion_id": completion_id})),
        )

    return completion_id


def _build_stats_report(household_id: int, *, all_time: bool = False) -> str:
    """
    Build a text leaderboard of chore completions.

    Monthly view: this-month counts only.
    All-time view: all-time totals with this-month counts in parentheses for context.

    Imbalance flag: if a user's count for a chore is < 20% of the per-person
    average (total / n_users), they get a gentle nudge. This flags only
    significant gaps — a mild 60/40 split won't trigger it.
    """
    users = get_household_users(household_id)
    if not users:
        return "No roommates found."

    user_map = {u["id"]: u["name"] for u in users}
    n_users = len(users)
    today = date.today()
    month_start = today.replace(day=1).isoformat()

    with get_conn() as conn:
        all_rows = conn.execute(
            "SELECT cc.chore_id, c.name AS chore_name, cc.user_id, cc.completed_at "
            "FROM chore_completions cc "
            "JOIN chores c ON c.id = cc.chore_id "
            "WHERE c.household_id = ?",
            (household_id,),
        ).fetchall()

    if not all_rows:
        period = "all time" if all_time else "this month"
        return f"No chores logged {period} yet.\nLog one: `/did dishes`"

    # Aggregate into {chore_name: {user_id: count}} for both all-time and month
    totals: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    monthly: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))

    for row in all_rows:
        totals[row["chore_name"]][row["user_id"]] += 1
        if row["completed_at"] >= month_start:
            monthly[row["chore_name"]][row["user_id"]] += 1

    counts = totals if all_time else monthly

    if not any(counts.values()):
        return (
            f"No chores logged this month yet.\n"
            "Log one: `/did dishes`\n\n"
            "_Run /chorestats all for all-time view._"
        )

    period_label = "All-time" if all_time else today.strftime("%B %Y")
    lines = [f"*Chore stats — {period_label}*\n"]
    nudges: list[str] = []

    for chore_name in sorted(counts):
        user_counts = counts[chore_name]
        total = sum(user_counts.values())
        if total == 0:
            continue

        avg = total / n_users
        lines.append(f"*{chore_name.title()}* ({total} time{'s' if total != 1 else ''})")

        for uid in sorted(user_counts, key=lambda u: -user_counts[u]):
            name = user_map.get(uid, f"user#{uid}")
            count = user_counts[uid]

            if all_time:
                month_count = monthly[chore_name].get(uid, 0)
                month_str = f"  _{month_count} this month_" if month_count else ""
                lines.append(f"  {name}: {count}{month_str}")
            else:
                lines.append(f"  {name}: {count}")

            if n_users >= 2 and count < 0.2 * avg:
                nudges.append(f"{name} is light on {chore_name}")

        # Show users who did zero this period
        for uid in user_map:
            if uid not in user_counts:
                name = user_map[uid]
                lines.append(f"  {name}: 0")
                if n_users >= 2 and avg > 0:
                    nudges.append(f"{name} hasn't done {chore_name} yet")

        lines.append("")

    if nudges:
        lines.append("_A gentle nudge:_")
        for nudge in nudges:
            lines.append(f"  ⚠️ {nudge}")
        lines.append("")

    if not all_time:
        lines.append("_Run /chorestats all for all-time view._")

    return "\n".join(lines)
