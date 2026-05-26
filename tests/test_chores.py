"""Tests for chore logging and stats report generation."""
import sqlite3
from datetime import date

from unittest.mock import AsyncMock, MagicMock

import pytest

import db as db_module
from db import _column_exists, get_conn, init_db
from handlers.chores import _build_stats_report, _log_chore, _normalize_chore_name, chore_cmd


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """
    Point db.DB_PATH at a fresh temp file for each test.

    db.get_conn() resolves DB_PATH from the module global at call time, so
    patching db_module.DB_PATH is enough — no import reload needed.
    """
    test_db = str(tmp_path / "test.db")
    original = db_module.DB_PATH
    db_module.DB_PATH = test_db
    init_db()
    yield
    db_module.DB_PATH = original


def _seed(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Insert a household + two users. Returns (household_id, user1_id, user2_id)."""
    hh_id = conn.execute(
        "INSERT INTO households (telegram_chat_id, name) VALUES (1, 'Test House')"
    ).lastrowid
    u1 = conn.execute(
        "INSERT INTO users (household_id, telegram_user_id, name) VALUES (?, 101, 'Alice')",
        (hh_id,),
    ).lastrowid
    u2 = conn.execute(
        "INSERT INTO users (household_id, telegram_user_id, name) VALUES (?, 102, 'Bob')",
        (hh_id,),
    ).lastrowid
    return hh_id, u1, u2


# --- _normalize_chore_name ---

def test_normalize_lowercase():
    assert _normalize_chore_name("Dishes") == "dishes"

def test_normalize_strips_the():
    assert _normalize_chore_name("the dishes") == "dishes"
    assert _normalize_chore_name("The Bathroom") == "bathroom"

def test_normalize_no_the_false_positive():
    # "theory" shouldn't be mangled
    assert _normalize_chore_name("theory") == "theory"

def test_normalize_strips_whitespace():
    assert _normalize_chore_name("  trash  ") == "trash"


# --- _log_chore ---

def test_log_chore_creates_chore_on_first_call():
    with get_conn() as conn:
        hh_id, u1, _ = _seed(conn)

    _log_chore(hh_id, u1, "dishes")

    with get_conn() as conn:
        chore = conn.execute(
            "SELECT * FROM chores WHERE household_id = ? AND name = 'dishes'", (hh_id,)
        ).fetchone()
        assert chore is not None


def test_log_chore_reuses_existing_chore():
    with get_conn() as conn:
        hh_id, u1, _ = _seed(conn)

    _log_chore(hh_id, u1, "trash")
    _log_chore(hh_id, u1, "trash")

    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM chores WHERE household_id = ? AND name = 'trash'", (hh_id,)
        ).fetchone()[0]
        assert count == 1  # one chore definition, two completions


def test_log_chore_inserts_completion():
    with get_conn() as conn:
        hh_id, u1, _ = _seed(conn)

    _log_chore(hh_id, u1, "dishes")
    _log_chore(hh_id, u1, "dishes")

    with get_conn() as conn:
        chore = conn.execute(
            "SELECT id FROM chores WHERE household_id = ? AND name = 'dishes'", (hh_id,)
        ).fetchone()
        completions = conn.execute(
            "SELECT COUNT(*) FROM chore_completions WHERE chore_id = ?", (chore["id"],)
        ).fetchone()[0]
        assert completions == 2


def test_log_chore_writes_action_log():
    with get_conn() as conn:
        hh_id, u1, _ = _seed(conn)

    _log_chore(hh_id, u1, "laundry")

    with get_conn() as conn:
        action = conn.execute(
            "SELECT * FROM actions WHERE household_id = ? AND action_type = 'chore_done'",
            (hh_id,),
        ).fetchone()
        assert action is not None


# --- _build_stats_report ---

def test_stats_empty():
    with get_conn() as conn:
        hh_id, _, _ = _seed(conn)

    report = _build_stats_report(hh_id)
    assert "No chores logged" in report


def test_stats_monthly_shows_this_month():
    with get_conn() as conn:
        hh_id, u1, u2 = _seed(conn)

    _log_chore(hh_id, u1, "dishes")
    _log_chore(hh_id, u1, "dishes")
    _log_chore(hh_id, u2, "dishes")

    report = _build_stats_report(hh_id, all_time=False)
    assert "dishes" in report.lower()
    assert "Alice" in report
    assert "Bob" in report


def test_stats_shows_zero_for_absent_user():
    with get_conn() as conn:
        hh_id, u1, u2 = _seed(conn)

    _log_chore(hh_id, u1, "trash")

    report = _build_stats_report(hh_id, all_time=False)
    # Bob did 0 — should still appear
    assert "Bob" in report
    assert "0" in report


def test_stats_alltime_includes_month_annotation():
    with get_conn() as conn:
        hh_id, u1, _ = _seed(conn)

    _log_chore(hh_id, u1, "bathroom")

    report = _build_stats_report(hh_id, all_time=True)
    assert "All-time" in report
    assert "this month" in report


def test_stats_nudge_fires_on_extreme_imbalance():
    """Nudge fires when one person did < 20% of the per-person average."""
    with get_conn() as conn:
        hh_id, u1, u2 = _seed(conn)

    # Alice does dishes 19 times, Bob does 0 — avg=9.5, 20% = 1.9
    # Bob: 0 < 1.9 → nudge expected
    for _ in range(19):
        _log_chore(hh_id, u1, "dishes")

    report = _build_stats_report(hh_id, all_time=False)
    assert "⚠️" in report
    assert "Bob" in report


def test_stats_nudge_absent_for_balanced_split():
    """No nudge for a 60/40 split — only extreme gaps get flagged."""
    with get_conn() as conn:
        hh_id, u1, u2 = _seed(conn)

    for _ in range(6):
        _log_chore(hh_id, u1, "dishes")
    for _ in range(4):
        _log_chore(hh_id, u2, "dishes")

    report = _build_stats_report(hh_id, all_time=False)
    # avg=5, 20% of 5 = 1.0; Bob has 4 > 1.0 → no nudge
    assert "⚠️" not in report


# --- migration guard ---

def test_column_exists_helper():
    with get_conn() as conn:
        assert _column_exists(conn, "households", "name")
        assert not _column_exists(conn, "households", "nonexistent_column")


def test_chore_completions_has_new_schema():
    """After migration 002, chore_completions should have user_id, not assigned_to_user_id."""
    with get_conn() as conn:
        assert _column_exists(conn, "chore_completions", "user_id")
        assert not _column_exists(conn, "chore_completions", "assigned_to_user_id")


# --- chore_cmd ---

def _make_update(chat_id: int, user_tg_id: int) -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user.id = user_tg_id
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args: list[str]) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args
    return ctx


async def test_chore_cmd_no_args_replies_usage():
    """No args → reply containing 'What chore did you do?'."""
    with get_conn() as conn:
        hh_id, u1, _ = _seed(conn)

    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context(args=[])

    await chore_cmd(update, ctx)

    update.message.reply_text.assert_called_once()
    call_kwargs = update.message.reply_text.call_args
    text = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("text", "")
    assert "What chore did you do?" in text


async def test_chore_cmd_no_household_replies_start_first():
    """Unknown chat_id → reply containing 'Run /start first'."""
    update = _make_update(chat_id=999, user_tg_id=101)
    ctx = _make_context(args=["dishes"])

    await chore_cmd(update, ctx)

    update.message.reply_text.assert_called_once()
    call_kwargs = update.message.reply_text.call_args
    text = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("text", "")
    assert "Run /start first" in text


async def test_chore_cmd_no_user_replies_join_first():
    """Household exists but unknown telegram_user_id → reply containing 'Join first'."""
    with get_conn() as conn:
        _seed(conn)  # seeds household for chat_id=1 with users 101 and 102

    update = _make_update(chat_id=1, user_tg_id=999)
    ctx = _make_context(args=["dishes"])

    await chore_cmd(update, ctx)

    update.message.reply_text.assert_called_once()
    call_kwargs = update.message.reply_text.call_args
    text = call_kwargs.args[0] if call_kwargs.args else call_kwargs.kwargs.get("text", "")
    assert "Join first" in text


async def test_chore_cmd_happy_path_inserts_completion():
    """Happy path → at least one row in chore_completions."""
    with get_conn() as conn:
        hh_id, _, _ = _seed(conn)

    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context(args=["dishes"])

    await chore_cmd(update, ctx)

    with get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM chore_completions").fetchone()[0]
    assert count >= 1


async def test_chore_cmd_happy_path_writes_action_log():
    """Happy path → actions table has a 'chore_done' row."""
    with get_conn() as conn:
        hh_id, _, _ = _seed(conn)

    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context(args=["dishes"])

    await chore_cmd(update, ctx)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM actions WHERE action_type = 'chore_done'"
        ).fetchone()
    assert row is not None


async def test_chore_cmd_normalization_stored_as_lowercase():
    """'The Dishes' passed as multi-word args → stored chore name is 'dishes'."""
    with get_conn() as conn:
        hh_id, _, _ = _seed(conn)

    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context(args=["The", "Dishes"])

    await chore_cmd(update, ctx)

    with get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM chores WHERE household_id = ?", (hh_id,)
        ).fetchone()
    assert row is not None
    assert row["name"] == "dishes"
