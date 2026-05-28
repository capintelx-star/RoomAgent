"""
Tests for the IntentResult Pydantic model and parse_message().

Pydantic-validation tests are sync (no API calls, no mocking needed).
parse_message() tests are async and mock _client with AsyncMock.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

import llm as llm_module
from llm import IntentResult, parse_message


def _make_response(tool_input: dict) -> MagicMock:
    """Build a minimal mock Anthropic response containing one tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = tool_input
    response = MagicMock()
    response.content = [block]
    return response


def test_purchase_intent_valid():
    result = IntentResult(
        intent="purchase",
        items="toilet paper, dish soap",
        amount_cents=1200,
        note="costco",
    )
    assert result.intent == "purchase"
    assert result.amount_cents == 1200
    assert result.items == "toilet paper, dish soap"


def test_ignore_intent_valid():
    result = IntentResult(intent="ignore")
    assert result.items is None
    assert result.amount_cents is None
    assert result.note is None


def test_invalid_intent_rejected():
    with pytest.raises(ValidationError):
        IntentResult(intent="dance")


def test_bill_log_intent_valid():
    result = IntentResult(
        intent="bill_log",
        bill_type="electric",
        amount_cents=8700,
    )
    assert result.intent == "bill_log"
    assert result.bill_type == "electric"
    assert result.amount_cents == 8700


def test_low_stock_intent_valid():
    result = IntentResult(intent="low_stock", item="dish soap")
    assert result.item == "dish soap"


def test_optional_fields_default_none():
    result = IntentResult(intent="ignore")
    assert result.category is None
    assert result.note is None
    assert result.bill_type is None
    assert result.item is None
    assert result.items is None


def test_query_balance_valid():
    result = IntentResult(intent="query_balance")
    assert result.intent == "query_balance"


def test_unknown_intent_valid():
    result = IntentResult(intent="unknown")
    assert result.intent == "unknown"


# --- parse_message() async tests ---

async def test_concurrent_parse_message_calls():
    """Two concurrent parse_message() calls return independent IntentResults.

    Validates that _last_llm_status mutation and result assignment don't
    bleed between coroutines running under asyncio.gather.
    """
    r_purchase = _make_response({"intent": "purchase", "items": "dish soap", "amount_cents": 400})
    r_chore = _make_response({"intent": "chore_done", "chore_name": "dishes"})

    with patch.object(llm_module, "_client") as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=[r_purchase, r_chore])
        result_a, result_b = await asyncio.gather(
            parse_message("got dish soap $4"),
            parse_message("did the dishes"),
        )

    assert result_a.intent == "purchase"
    assert result_a.amount_cents == 400
    assert result_b.intent == "chore_done"
    assert result_b.chore_name == "dishes"
