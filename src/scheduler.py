"""
Scheduler jobs (all UTC):
  - 09:00 daily: rent reminders for households with upcoming due dates
  - 03:00 daily: SQLite backup to backups/roomagent.db.YYYY-MM-DD.bak (7-day retention)

Edge case note: if rent due_day <= 3, the 3-day reminder falls in the prior
month and is skipped for MVP. See DEVLOG.md.
"""
import shutil
from datetime import date, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot

_scheduler = AsyncIOScheduler(timezone="UTC")


async def _rent_reminder_job(bot: Bot) -> None:
    """Check all households and fire rent reminders where appropriate."""
    from db import get_conn  # imported here to avoid circular import at module load

    today = date.today()

    with get_conn() as conn:
        reminders = conn.execute(
            "SELECT h.telegram_chat_id, h.name, rb.amount_cents, rb.due_day "
            "FROM recurring_bills rb "
            "JOIN households h ON h.id = rb.household_id "
            "WHERE rb.is_rent = 1",
        ).fetchall()

    for r in reminders:
        due_day: int = r["due_day"]
        days_until: int = due_day - today.day
        amount: float = r["amount_cents"] / 100
        chat_id: int = r["telegram_chat_id"]

        if days_until == 3:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Heads up: rent is due in 3 days (the {due_day}{_ordinal(due_day)}).\n"
                    f"Amount: ${amount:,.2f}\n\n"
                    "Run /rent for details."
                ),
            )
        elif days_until == 0:
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    f"Rent is due today! ${amount:,.2f}\n\n"
                    "Log it once someone pays: `paid rent $X`"
                ),
            )


async def _db_backup_job() -> None:
    """Copy the live DB to backups/roomagent.db.YYYY-MM-DD.bak, keep last 7 days."""
    from config import DB_PATH  # imported here to avoid circular import at load time

    db_path = Path(DB_PATH)
    if not db_path.exists():
        return

    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    today = date.today()
    shutil.copy2(db_path, backup_dir / f"roomagent.db.{today.isoformat()}.bak")

    cutoff = today - timedelta(days=7)
    for f in backup_dir.glob("roomagent.db.*.bak"):
        try:
            file_date = date.fromisoformat(f.stem.split(".", 2)[2])
            if file_date < cutoff:
                f.unlink()
        except ValueError:
            pass  # ignore files with unexpected names


def start_scheduler(bot: Bot) -> None:
    """Register all scheduled jobs and start the scheduler."""
    _scheduler.add_job(
        _rent_reminder_job,
        trigger="cron",
        hour=9,
        minute=0,
        args=[bot],
        id="rent_reminder",
        replace_existing=True,
    )
    _scheduler.add_job(
        _db_backup_job,
        trigger="cron",
        hour=3,
        minute=0,
        id="db_backup",
        replace_existing=True,
    )
    _scheduler.start()


def stop_scheduler() -> None:
    """Shut down the scheduler cleanly on bot exit."""
    if _scheduler.running:
        _scheduler.shutdown(wait=False)


def _ordinal(n: int) -> str:
    if 11 <= n % 100 <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
