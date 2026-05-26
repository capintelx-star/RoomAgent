"""
Tests for the IntentResult Pydantic model.

Validates the schema without making any API calls.
"""
import pytest
from pydantic import ValidationError

from llm import IntentResult


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
