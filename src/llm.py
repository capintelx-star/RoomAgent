"""
Claude API wrapper for intent parsing.

Uses Anthropic tool use so Claude fills in a typed "form" (our Pydantic schema)
rather than returning free-text JSON we'd have to parse ourselves.
Every response is validated with Pydantic before touching the DB.
"""
import logging
from datetime import datetime, timezone
from typing import Literal

import anthropic
from pydantic import BaseModel, ValidationError

from config import ANTHROPIC_API_KEY

logger = logging.getLogger(__name__)
# AsyncAnthropic binds its httpx.AsyncClient lazily (not at __init__ time), so this
# module-level singleton is loop-safe at import time. Do NOT wrap calls in asyncio.to_thread.
_client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
MODEL = "claude-sonnet-4-5"

_last_llm_status: str = "never called"


def get_last_llm_status() -> str:
    return _last_llm_status


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S UTC")


class IntentResult(BaseModel):
    """Structured result from Claude's message classification."""

    intent: Literal[
        "purchase", "bill_log", "low_stock", "query_balance",
        "chore_done", "ignore", "unknown"
    ]
    # purchase / bill_log
    items: str | None = None         # "toilet paper, dish soap" (combined)
    category: str | None = None      # "bathroom", "kitchen", "cleaning", etc.
    amount_cents: int | None = None  # $12.50 → 1250
    note: str | None = None          # store name or extra context
    # low_stock
    item: str | None = None          # the specific item flagged as low
    # bill_log
    bill_type: str | None = None     # "electric", "gas", "water", "internet", etc.
    # chore_done
    chore_name: str | None = None    # "dishes", "trash", "bathroom", etc.
    # set on error; handlers receive intent="unknown" and can inspect this
    error: str | None = None


# Tool definition: the "form" Claude fills in.
_TOOL: dict = {
    "name": "classify_message",
    "description": "Classify a roommate group chat message into a structured intent.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": [
                    "purchase", "bill_log", "low_stock", "query_balance",
                    "chore_done", "ignore", "unknown",
                ],
                "description": (
                    "Message intent. Use 'ignore' liberally for chitchat, "
                    "greetings, reactions, off-topic messages."
                ),
            },
            "items": {
                "type": "string",
                "description": (
                    "For purchase: all items combined into one string. "
                    "'TP and dish soap' → 'toilet paper, dish soap'."
                ),
            },
            "category": {
                "type": "string",
                "description": "Item category: bathroom, kitchen, cleaning, food, etc.",
            },
            "amount_cents": {
                "type": "integer",
                "description": "Amount in cents. '$12.50' → 1250. '$18' → 1800.",
            },
            "note": {
                "type": "string",
                "description": "Store name or extra context (e.g. 'Costco').",
            },
            "item": {
                "type": "string",
                "description": "For low_stock: the single item that is running low.",
            },
            "bill_type": {
                "type": "string",
                "description": "For bill_log: electric, gas, water, internet, etc.",
            },
            "chore_name": {
                "type": "string",
                "description": (
                    "For chore_done: the canonical chore name in lowercase singular form. "
                    "'did the dishes' → 'dishes'. 'took out trash' → 'trash'. "
                    "'cleaned the bathroom' → 'bathroom'."
                ),
            },
        },
        "required": ["intent"],
    },
}

_SYSTEM = (
    "You parse messages from a roommate group chat into structured intents.\n\n"
    "Intent rules:\n"
    "- purchase: someone bought shared household supplies "
    "(keywords: got, bought, grabbed, picked up, paid for)\n"
    "- bill_log: someone paid a utility or recurring bill "
    "(electric, gas, water, internet, etc.)\n"
    "- low_stock: something is running out or needs to be bought\n"
    "- query_balance: asking about money owed or who owes what\n"
    "- chore_done: someone completed a household chore — dishes, trash, laundry, "
    "bathroom, vacuuming, sweeping, mopping, cleaning, etc. "
    "(keywords: did, done, cleaned, washed, swept, mopped, vacuumed, took out)\n"
    "- ignore: chitchat, greetings, reactions, off-topic — USE THIS LIBERALLY\n"
    "- unknown: genuinely ambiguous, not classifiable\n\n"
    "Amount rule: always convert to integer cents. '$12.50' → 1250. '$18' → 1800.\n"
    "Items rule: combine all items into one descriptive string.\n"
    "Chore name rule: return lowercase singular form. "
    "'did the dishes' → chore_name='dishes'. 'took out trash' → 'trash'."
)


async def parse_message(text: str) -> IntentResult:
    """
    Send a message to Claude and return a validated IntentResult.

    Forces tool use so we always get structured output, never free text.
    Falls back to intent='unknown' on any failure; sets .error for callers
    that want to distinguish transient API errors from clean unknowns.
    """
    global _last_llm_status
    try:
        response = await _client.messages.create(
            model=MODEL,
            max_tokens=512,
            system=_SYSTEM,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "classify_message"},
            messages=[{"role": "user", "content": text}],
        )
        for block in response.content:
            if block.type == "tool_use":
                result = IntentResult.model_validate(block.input)
                _last_llm_status = f"ok ({_ts()})"
                return result
    except anthropic.AuthenticationError as e:
        logger.error("LLM auth error — check ANTHROPIC_API_KEY: %s", e)
        _last_llm_status = f"auth error ({_ts()})"
        return IntentResult(intent="unknown", error=f"auth: {e}")
    except anthropic.APIError as e:
        logger.warning("LLM API error: %s", e)
        _last_llm_status = f"api error ({_ts()})"
        return IntentResult(intent="unknown", error=f"api: {e}")
    except ValidationError as e:
        logger.warning("LLM response failed validation: %s", e)
        _last_llm_status = f"parse error ({_ts()})"
        return IntentResult(intent="unknown", error=f"parse: {e}")
    _last_llm_status = f"no tool block ({_ts()})"
    return IntentResult(intent="unknown")
