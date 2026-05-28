"""Roomagent bot — entry point and handler registration."""
import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from config import TELEGRAM_BOT_TOKEN
from db import init_db
from handlers.bills import owe_cmd, rent_cmd
from handlers.chores import chore_cmd, chorestats_cmd, did_cmd
from handlers.health import health_cmd
from handlers.onboarding import (
    help_cmd,
    make_join_handler,
    make_start_handler,
    undo_cmd,
)
from handlers.settle import settle_cmd
from handlers.supplies import bought_cmd, free_text_handler, need_cmd

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def _post_init(app: Application) -> None:
    """Start the APScheduler after the bot's event loop is running."""
    from scheduler import start_scheduler
    start_scheduler(app.bot)
    logger.info("Scheduler started.")


async def _post_shutdown(app: Application) -> None:
    """Stop the scheduler cleanly when the bot exits."""
    from scheduler import stop_scheduler
    stop_scheduler()


def main() -> None:
    init_db()
    logger.info("Database initialized.")

    app = (
        Application.builder()
        .token(TELEGRAM_BOT_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    # Onboarding ConversationHandlers — registered first so they take priority
    app.add_handler(make_start_handler())
    app.add_handler(make_join_handler())

    # Slash commands (deterministic — no LLM)
    app.add_handler(CommandHandler("bought", bought_cmd))
    app.add_handler(CommandHandler("need", need_cmd))
    app.add_handler(CommandHandler("rent", rent_cmd))
    app.add_handler(CommandHandler("owe", owe_cmd))
    app.add_handler(CommandHandler("did", did_cmd))
    app.add_handler(CommandHandler("chore", chore_cmd))
    app.add_handler(CommandHandler("chorestats", chorestats_cmd))
    app.add_handler(CommandHandler("settle", settle_cmd))
    app.add_handler(CommandHandler("undo", undo_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("health", health_cmd))

    # Free text — prefilter → Claude → domain handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_text_handler))

    logger.info("Bot polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
