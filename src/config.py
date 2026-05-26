"""Loads environment variables from .env and exposes them as module-level constants."""
import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN: str = os.environ["TELEGRAM_BOT_TOKEN"]
ANTHROPIC_API_KEY: str = os.environ["ANTHROPIC_API_KEY"]
AMAZON_ASSOCIATE_TAG: str = os.environ.get("AMAZON_ASSOCIATE_TAG", "")
DB_PATH: str = os.environ.get("DB_PATH", "roomagent.db")
