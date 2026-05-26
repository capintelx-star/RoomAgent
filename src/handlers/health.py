"""Health check command — bot version, DB counts, backup status, LLM status."""
import logging
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from config import DB_PATH
from db import get_conn
from llm import get_last_llm_status

logger = logging.getLogger(__name__)

_TABLES = [
    "households", "users", "purchases", "supplies",
    "recurring_bills", "bills", "bill_shares",
    "chores", "chore_completions", "actions",
]


def _bot_version() -> str:
    try:
        return version("roomagent")
    except PackageNotFoundError:
        return "unknown (not installed as package)"


def _db_counts() -> str:
    try:
        with get_conn() as conn:
            lines = []
            for table in _TABLES:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                lines.append(f"  {table}: {count}")
            return "\n".join(lines)
    except Exception as e:
        logger.warning("health: DB count error: %s", e)
        return f"  error reading DB: {e}"


def _last_backup() -> str:
    db_path = Path(DB_PATH)
    backup_dir = db_path.parent / "backups"
    if not backup_dir.exists():
        return "no backups directory"
    baks = sorted(backup_dir.glob("roomagent.db.*.bak"))
    return baks[-1].name if baks else "none yet"


async def health_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        f"Roomagent health\n"
        f"Version: {_bot_version()}\n"
        f"\n"
        f"DB row counts:\n{_db_counts()}\n"
        f"\n"
        f"Last backup: {_last_backup()}\n"
        f"Last LLM call: {get_last_llm_status()}"
    )
    await update.message.reply_text(text)
