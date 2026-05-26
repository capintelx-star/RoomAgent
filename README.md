# Roomagent

A Telegram bot that helps roommates track shared supplies and bills.

## Setup

### 1. Prerequisites

- Python 3.11+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An [Anthropic API key](https://console.anthropic.com/)
- (Optional) An Amazon Associates tag for affiliate links

### 2. Install dependencies

```bash
pip install -e ".[dev]"
```

This installs the bot and all dev tools (pytest, etc.) in "editable" mode —
meaning changes to the source files take effect immediately without reinstalling.

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your actual tokens
```

### 4. Run the tests

```bash
pytest
```

All tests should pass before you run the bot.

### 5. Run the bot

```bash
roomagent
```

Or directly:

```bash
python src/bot.py
```

## Commands

| Command | What it does |
|---|---|
| `/start` | Register this group chat as a household |
| `/join <name> [venmo_handle]` | Add yourself as a roommate |
| `/bought <text>` | Log a purchase (e.g. `/bought TP $12 Costco`) |
| `/need <item>` | Flag something as low stock, returns Amazon link |
| `/rent` | Show this month's rent status |
| `/owe` | DMs you the current balance breakdown |
| `/undo` | Reverse your last action |
| `/help` | Show this list |

Free text also works — just mention the bot or use keywords like "bought", "paid", "got".

## Project structure

```
src/
  bot.py          — entry point, registers all handlers
  config.py       — loads .env values
  db.py           — SQLite connection and schema migration
  llm.py          — Claude API wrapper for intent parsing
  handlers/       — one file per feature area
  utils/
    prefilter.py  — regex gate before invoking Claude
    splits.py     — debt simplification algorithm
    amazon.py     — affiliate link builder
migrations/
  001_init.sql    — full database schema
tests/
```
