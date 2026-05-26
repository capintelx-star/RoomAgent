"""Tests for the LLM prefilter — no external services needed."""
import pytest
from utils.prefilter import should_invoke_llm


# --- Messages that SHOULD trigger LLM ---

def test_dollar_sign_triggers():
    assert should_invoke_llm("got milk $5")

def test_dollar_amount_triggers():
    assert should_invoke_llm("paper towels 12.99")

def test_bought_triggers():
    assert should_invoke_llm("I bought some TP")

def test_got_triggers():
    assert should_invoke_llm("got dish soap at Target")

def test_paid_triggers():
    assert should_invoke_llm("paid the electric bill")

def test_grabbed_triggers():
    assert should_invoke_llm("grabbed paper towels on my way home")

def test_picked_up_triggers():
    assert should_invoke_llm("picked up some snacks, like $8")

def test_out_of_triggers():
    assert should_invoke_llm("we're out of toilet paper")

def test_need_triggers():
    assert should_invoke_llm("we need more dish soap")

def test_mention_triggers():
    assert should_invoke_llm("@roombot what do we need?", bot_username="roombot")

def test_mention_case_insensitive():
    assert should_invoke_llm("@RoomBot help", bot_username="roombot")

def test_reply_to_bot_triggers():
    assert should_invoke_llm("yes", is_reply_to_bot=True)

def test_reply_triggers_even_chitchat():
    assert should_invoke_llm("lol", is_reply_to_bot=True)


# --- Messages that should NOT trigger LLM ---

def test_chitchat_ignored():
    assert not should_invoke_llm("lol same")

def test_reaction_ignored():
    assert not should_invoke_llm("haha nice")

def test_greeting_ignored():
    assert not should_invoke_llm("hey everyone")

def test_agreement_ignored():
    assert not should_invoke_llm("sounds good!")

def test_unrelated_mention_ignored():
    assert not should_invoke_llm("@otherbot do something", bot_username="roombot")

def test_empty_string_ignored():
    assert not should_invoke_llm("")
