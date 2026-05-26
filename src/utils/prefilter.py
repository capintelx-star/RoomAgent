"""
Regex gate that decides whether a free-text message should be sent to Claude.

The LLM is only invoked when:
  1. The bot is directly @-mentioned
  2. The message is a reply to a bot message
  3. The message matches purchase/bill keywords or dollar amounts

Everything else is ignored — chitchat, reactions, off-topic messages.
"""
import re

# Matches: bare $ sign, "$12" style amounts, plain numbers with cents ("12.34"),
# purchase/stock keywords, or household chore action words.
_TRIGGER = re.compile(
    r"\$\d"                              # $12, $5, etc.
    r"|\d+\.\d{2}"                       # 12.34 (dollar amounts with cents)
    r"|\b(?:got|bought|paid|grabbed"     # purchase verbs
    r"|picked\s+up"                      # multi-word
    r"|out\s+of|we(?:'re|\s+are)\s+out" # low-stock phrases
    r"|need"                             # low-stock
    r"|cleaned|swept|mopped|vacuumed"   # chore action verbs
    r"|took\s+out"                       # "took out the trash"
    r"|dishes|laundry|trash|garbage"    # common chore nouns
    r")\b",
    re.IGNORECASE,
)


def should_invoke_llm(
    text: str,
    bot_username: str | None = None,
    is_reply_to_bot: bool = False,
) -> bool:
    """Return True if this message warrants an LLM call."""
    if is_reply_to_bot:
        return True
    if bot_username and f"@{bot_username}".lower() in text.lower():
        return True
    return bool(_TRIGGER.search(text))
