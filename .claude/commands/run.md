# /run — run tests then start the bot

Run the full test suite. If any tests fail, stop and report — do not start the bot. If all tests pass, start roomagent.

## Steps

**1. Run pytest**

```bash
cd C:\Users\twajs\OneDrive\Documents\ai-projects\roomagent
pytest tests/ -v
```

Check the exit code. If non-zero (any test failed), stop here and show the failure output. Do not proceed to step 2.

**2. Start the bot**

```bash
roomagent
```

Or if the entry point script isn't on PATH:

```bash
python src/bot.py
```

The bot needs `.env` populated with `TELEGRAM_BOT_TOKEN` and `ANTHROPIC_API_KEY`. If the bot crashes on startup with a missing env var or token error, show the error and stop.

## What to report

- Number of tests collected and passed/failed
- If all green: confirm the bot is starting, then stream its log output
- If red: paste the failing test names and tracebacks; do not start the bot
