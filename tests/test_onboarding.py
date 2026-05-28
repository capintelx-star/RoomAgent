"""Tests for the /start leader flow and /join member flow."""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import db as db_module
from db import _column_exists, get_conn, init_db
from handlers.onboarding import (
    LEADER_CHORES,
    LEADER_CLARIFY,
    LEADER_RENT_DAY,
    MEMBER_NAME,
    MEMBER_RENT,
    MEMBER_VENMO,
    _cancel,
    _complete_join,
    _finish_leader_setup,
    _join_entry,
    _leader_chores,
    _leader_clarify,
    _leader_rent_day,
    _member_name,
    _member_rent,
    _member_venmo,
    _parse_join_args,
    _setup_entry,
    _start_entry,
)
from telegram.ext import ConversationHandler


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    original = db_module.DB_PATH
    db_module.DB_PATH = str(tmp_path / "test.db")
    init_db()
    yield
    db_module.DB_PATH = original


def _make_update(chat_id: int = 1, user_tg_id: int = 101, chat_title: str = "Test House") -> MagicMock:
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_chat.title = chat_title
    update.effective_user.id = user_tg_id
    update.message.reply_text = AsyncMock()
    update.message.text = None
    return update


def _make_context(args: list[str] | None = None, user_data: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.args = args or []
    ctx.user_data = user_data or {}
    return ctx


def _seed_household(conn, chat_id: int = 1, leader_tg_id: int = 101) -> int:
    return conn.execute(
        "INSERT INTO households (telegram_chat_id, name, leader_telegram_user_id) "
        "VALUES (?, 'Test House', ?)",
        (chat_id, leader_tg_id),
    ).lastrowid


def _seed_user(conn, household_id: int, tg_id: int = 101, name: str = "Alice",
               rent_cents: int = 90000, is_leader: int = 0) -> int:
    return conn.execute(
        "INSERT INTO users (household_id, telegram_user_id, name, rent_amount_cents, is_leader) "
        "VALUES (?, ?, ?, ?, ?)",
        (household_id, tg_id, name, rent_cents, is_leader),
    ).lastrowid


# ── Migration 004 ────────────────────────────────────────────────────────────

def test_migration_004_adds_rent_amount_cents():
    with get_conn() as conn:
        assert _column_exists(conn, "users", "rent_amount_cents")


def test_migration_004_adds_is_leader():
    with get_conn() as conn:
        assert _column_exists(conn, "users", "is_leader")


def test_migration_004_adds_households_rent_due_day():
    with get_conn() as conn:
        assert _column_exists(conn, "households", "rent_due_day")


def test_migration_004_adds_leader_telegram_user_id():
    with get_conn() as conn:
        assert _column_exists(conn, "households", "leader_telegram_user_id")


# ── _parse_join_args ─────────────────────────────────────────────────────────

def test_parse_join_args_empty():
    assert _parse_join_args([]) == (None, None, None)


def test_parse_join_args_name_only():
    name, rent, venmo = _parse_join_args(["Thomas"])
    assert name == "Thomas"
    assert rent is None
    assert venmo is None


def test_parse_join_args_name_and_amount():
    name, rent, venmo = _parse_join_args(["Thomas", "900"])
    assert name == "Thomas"
    assert rent == 90000
    assert venmo is None


def test_parse_join_args_all_three():
    name, rent, venmo = _parse_join_args(["Thomas", "900", "@thomas_v"])
    assert name == "Thomas"
    assert rent == 90000
    assert venmo == "@thomas_v"


def test_parse_join_args_dollar_prefix():
    _, rent, _ = _parse_join_args(["Alice", "$1200.50"])
    assert rent == 120050


def test_parse_join_args_non_numeric_amount():
    name, rent, venmo = _parse_join_args(["Thomas", "@venmo_only"])
    assert name == "Thomas"
    assert rent is None  # old-style format — can't extract amount


def test_parse_join_args_zero_amount():
    _, rent, _ = _parse_join_args(["Thomas", "0"])
    assert rent is None  # 0 is not a valid rent amount


# ── /start entry ─────────────────────────────────────────────────────────────

async def test_start_entry_creates_household():
    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context()

    state = await _start_entry(update, ctx)

    assert state == LEADER_RENT_DAY
    with get_conn() as conn:
        hh = conn.execute("SELECT * FROM households WHERE telegram_chat_id = 1").fetchone()
    assert hh is not None
    assert hh["leader_telegram_user_id"] == 101


async def test_start_entry_already_registered():
    with get_conn() as conn:
        _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1)
    ctx = _make_context()

    state = await _start_entry(update, ctx)

    assert state == ConversationHandler.END
    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.call_args.args[0]
    assert "already registered" in text


async def test_start_entry_stores_household_id_in_user_data():
    update = _make_update(chat_id=1)
    ctx = _make_context()

    await _start_entry(update, ctx)

    assert "household_id" in ctx.user_data
    assert ctx.user_data["is_setup"] is False


# ── /setup entry ─────────────────────────────────────────────────────────────

async def test_setup_entry_no_household():
    update = _make_update(chat_id=999)
    ctx = _make_context()

    state = await _setup_entry(update, ctx)

    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "/start" in text


async def test_setup_entry_not_joined():
    with get_conn() as conn:
        _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=999)
    ctx = _make_context()

    state = await _setup_entry(update, ctx)

    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "/join" in text


async def test_setup_entry_not_leader():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)
        _seed_user(conn, hh_id, tg_id=102, name="Bob", is_leader=0)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context()

    state = await _setup_entry(update, ctx)

    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "leader" in text.lower()


async def test_setup_entry_is_leader_proceeds():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)
        _seed_user(conn, hh_id, tg_id=101, name="Alice", is_leader=1)

    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context()

    state = await _setup_entry(update, ctx)

    assert state == LEADER_RENT_DAY
    assert ctx.user_data["is_setup"] is True


# ── _leader_rent_day ─────────────────────────────────────────────────────────

async def test_leader_rent_day_valid():
    update = _make_update()
    update.message.text = "1"
    ctx = _make_context(user_data={"household_id": 1})

    state = await _leader_rent_day(update, ctx)

    assert state == LEADER_CHORES
    assert ctx.user_data["rent_due_day"] == 1


async def test_leader_rent_day_invalid_zero():
    update = _make_update()
    update.message.text = "0"
    ctx = _make_context(user_data={"household_id": 1})

    state = await _leader_rent_day(update, ctx)

    assert state == LEADER_RENT_DAY
    update.message.reply_text.assert_called_once()


async def test_leader_rent_day_invalid_29():
    update = _make_update()
    update.message.text = "29"
    ctx = _make_context(user_data={})

    state = await _leader_rent_day(update, ctx)

    assert state == LEADER_RENT_DAY


async def test_leader_rent_day_non_numeric():
    update = _make_update()
    update.message.text = "first"
    ctx = _make_context(user_data={})

    state = await _leader_rent_day(update, ctx)

    assert state == LEADER_RENT_DAY


async def test_leader_rent_day_boundary_28():
    update = _make_update()
    update.message.text = "28"
    ctx = _make_context(user_data={"household_id": 1})

    state = await _leader_rent_day(update, ctx)

    assert state == LEADER_CHORES
    assert ctx.user_data["rent_due_day"] == 28


# ── _leader_chores ───────────────────────────────────────────────────────────

async def test_leader_chores_skip():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    update.message.text = "skip"
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 1, "is_setup": False})

    state = await _leader_chores(update, ctx)

    assert state == ConversationHandler.END
    update.message.reply_text.assert_called_once()


async def test_leader_chores_confident_parse():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    update.message.text = "trash, dishes, bathroom"
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 1, "is_setup": False})

    with patch("handlers.onboarding.parse_chores", return_value=(["trash", "dishes", "bathroom"], True)):
        state = await _leader_chores(update, ctx)

    assert state == ConversationHandler.END
    with get_conn() as conn:
        chores = conn.execute(
            "SELECT name FROM chores WHERE household_id = ?", (hh_id,)
        ).fetchall()
    assert {c["name"] for c in chores} == {"trash", "dishes", "bathroom"}


async def test_leader_chores_unconfident_asks_clarification():
    update = _make_update()
    update.message.text = "????"
    ctx = _make_context(user_data={"household_id": 1, "rent_due_day": 1})

    with patch("handlers.onboarding.parse_chores", return_value=([], False)):
        state = await _leader_chores(update, ctx)

    assert state == LEADER_CLARIFY


async def test_leader_chores_confident_but_empty_asks_clarification():
    update = _make_update()
    update.message.text = "something"
    ctx = _make_context(user_data={"household_id": 1, "rent_due_day": 1})

    with patch("handlers.onboarding.parse_chores", return_value=([], True)):
        state = await _leader_chores(update, ctx)

    assert state == LEADER_CLARIFY


# ── _leader_clarify ──────────────────────────────────────────────────────────

async def test_leader_clarify_skip():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    update.message.text = "skip"
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 5, "is_setup": False})

    state = await _leader_clarify(update, ctx)

    assert state == ConversationHandler.END


async def test_leader_clarify_still_unconfident_skips_chores():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    update.message.text = "still unclear"
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 1, "is_setup": False})

    with patch("handlers.onboarding.parse_chores", return_value=([], False)):
        state = await _leader_clarify(update, ctx)

    assert state == ConversationHandler.END
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM chores WHERE household_id = ?", (hh_id,)
        ).fetchone()[0]
    assert count == 0  # no chores inserted


async def test_leader_clarify_second_parse_succeeds():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    update.message.text = "trash and dishes"
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 1, "is_setup": False})

    with patch("handlers.onboarding.parse_chores", return_value=(["trash", "dishes"], True)):
        state = await _leader_clarify(update, ctx)

    assert state == ConversationHandler.END
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM chores WHERE household_id = ?", (hh_id,)
        ).fetchone()[0]
    assert count == 2


# ── _finish_leader_setup ─────────────────────────────────────────────────────

async def test_finish_leader_setup_writes_rent_due_day():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 15, "is_setup": False})

    await _finish_leader_setup(update, ctx, chore_names=[])

    with get_conn() as conn:
        hh = conn.execute("SELECT rent_due_day FROM households WHERE id = ?", (hh_id,)).fetchone()
    assert hh["rent_due_day"] == 15


async def test_finish_leader_setup_inserts_chores():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 1, "is_setup": False})

    await _finish_leader_setup(update, ctx, chore_names=["Trash", "DISHES"])

    with get_conn() as conn:
        chores = conn.execute(
            "SELECT name FROM chores WHERE household_id = ?", (hh_id,)
        ).fetchall()
    assert {c["name"] for c in chores} == {"trash", "dishes"}


async def test_finish_leader_setup_idempotent_chore_insert():
    """Running setup twice doesn't duplicate chores."""
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update()
    ctx = _make_context(user_data={"household_id": hh_id, "rent_due_day": 1, "is_setup": False})

    await _finish_leader_setup(update, ctx, chore_names=["trash"])
    await _finish_leader_setup(update, ctx, chore_names=["trash"])

    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM chores WHERE household_id = ?", (hh_id,)
        ).fetchone()[0]
    assert count == 1


# ── /join entry ──────────────────────────────────────────────────────────────

async def test_join_entry_no_household():
    update = _make_update(chat_id=999)
    ctx = _make_context()

    state = await _join_entry(update, ctx)

    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "leader" in text.lower()


async def test_join_entry_already_joined():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)
        _seed_user(conn, hh_id, tg_id=101)

    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context()

    state = await _join_entry(update, ctx)

    assert state == ConversationHandler.END
    text = update.message.reply_text.call_args.args[0]
    assert "already" in text


async def test_join_entry_no_args_asks_name():
    with get_conn() as conn:
        _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context(args=[])

    state = await _join_entry(update, ctx)

    assert state == MEMBER_NAME


async def test_join_entry_name_only_asks_rent():
    with get_conn() as conn:
        _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context(args=["Thomas"])

    state = await _join_entry(update, ctx)

    assert state == MEMBER_RENT
    assert ctx.user_data["join_name"] == "Thomas"


async def test_join_entry_power_user_shortcut_completes():
    with get_conn() as conn:
        _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context(args=["Thomas", "900", "@thomas_v"])

    state = await _join_entry(update, ctx)

    assert state == ConversationHandler.END
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = 102"
        ).fetchone()
    assert user is not None
    assert user["name"] == "Thomas"
    assert user["rent_amount_cents"] == 90000
    assert user["venmo_handle"] == "@thomas_v"


async def test_join_entry_power_user_sets_is_leader_for_leader():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1, leader_tg_id=101)

    update = _make_update(chat_id=1, user_tg_id=101)
    ctx = _make_context(args=["Alice", "1200"])

    state = await _join_entry(update, ctx)

    assert state == ConversationHandler.END
    with get_conn() as conn:
        user = conn.execute(
            "SELECT is_leader FROM users WHERE telegram_user_id = 101"
        ).fetchone()
    assert user["is_leader"] == 1


async def test_join_entry_non_leader_gets_is_leader_false():
    with get_conn() as conn:
        _seed_household(conn, chat_id=1, leader_tg_id=101)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context(args=["Bob", "800"])

    await _join_entry(update, ctx)

    with get_conn() as conn:
        user = conn.execute(
            "SELECT is_leader FROM users WHERE telegram_user_id = 102"
        ).fetchone()
    assert user["is_leader"] == 0


# ── _member_name ─────────────────────────────────────────────────────────────

async def test_member_name_stores_and_advances():
    update = _make_update()
    update.message.text = "Thomas"
    ctx = _make_context(user_data={"join_household_id": 1})

    state = await _member_name(update, ctx)

    assert state == MEMBER_RENT
    assert ctx.user_data["join_name"] == "Thomas"


async def test_member_name_empty_stays():
    update = _make_update()
    update.message.text = "   "
    ctx = _make_context(user_data={})

    state = await _member_name(update, ctx)

    assert state == MEMBER_NAME


# ── _member_rent ─────────────────────────────────────────────────────────────

async def test_member_rent_valid():
    update = _make_update()
    update.message.text = "900"
    ctx = _make_context(user_data={})

    state = await _member_rent(update, ctx)

    assert state == MEMBER_VENMO
    assert ctx.user_data["join_rent_cents"] == 90000


async def test_member_rent_with_dollar_sign():
    update = _make_update()
    update.message.text = "$1200.50"
    ctx = _make_context(user_data={})

    state = await _member_rent(update, ctx)

    assert state == MEMBER_VENMO
    assert ctx.user_data["join_rent_cents"] == 120050


async def test_member_rent_zero_invalid():
    update = _make_update()
    update.message.text = "0"
    ctx = _make_context(user_data={})

    state = await _member_rent(update, ctx)

    assert state == MEMBER_RENT


async def test_member_rent_negative_invalid():
    update = _make_update()
    update.message.text = "-500"
    ctx = _make_context(user_data={})

    state = await _member_rent(update, ctx)

    assert state == MEMBER_RENT


async def test_member_rent_non_numeric():
    update = _make_update()
    update.message.text = "nine hundred"
    ctx = _make_context(user_data={})

    state = await _member_rent(update, ctx)

    assert state == MEMBER_RENT


# ── _member_venmo ────────────────────────────────────────────────────────────

async def test_member_venmo_skip_completes():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    update.message.text = "skip"
    ctx = _make_context(user_data={
        "join_household_id": hh_id,
        "join_name": "Thomas",
        "join_rent_cents": 90000,
        "join_is_leader": False,
    })

    state = await _member_venmo(update, ctx)

    assert state == ConversationHandler.END
    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = 102"
        ).fetchone()
    assert user["name"] == "Thomas"
    assert user["venmo_handle"] is None
    assert user["rent_amount_cents"] == 90000


async def test_member_venmo_sets_handle():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    update.message.text = "@thomas_v"
    ctx = _make_context(user_data={
        "join_household_id": hh_id,
        "join_name": "Thomas",
        "join_rent_cents": 90000,
        "join_is_leader": False,
    })

    await _member_venmo(update, ctx)

    with get_conn() as conn:
        user = conn.execute(
            "SELECT venmo_handle FROM users WHERE telegram_user_id = 102"
        ).fetchone()
    assert user["venmo_handle"] == "@thomas_v"


# ── _complete_join ───────────────────────────────────────────────────────────

async def test_complete_join_inserts_user():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context(user_data={"join_household_id": hh_id, "join_is_leader": False})

    await _complete_join(update, ctx, name="Thomas", rent_cents=90000, venmo="@t")

    with get_conn() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = 102"
        ).fetchone()
    assert user is not None
    assert user["rent_amount_cents"] == 90000


async def test_complete_join_writes_action_log():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context(user_data={"join_household_id": hh_id, "join_is_leader": False})

    await _complete_join(update, ctx, name="Thomas", rent_cents=90000, venmo=None)

    with get_conn() as conn:
        action = conn.execute(
            "SELECT * FROM actions WHERE action_type = 'user_join'"
        ).fetchone()
    assert action is not None
    payload = json.loads(action["payload_json"])
    assert "user_id" in payload


async def test_complete_join_confirmation_includes_rent():
    with get_conn() as conn:
        hh_id = _seed_household(conn, chat_id=1)

    update = _make_update(chat_id=1, user_tg_id=102)
    ctx = _make_context(user_data={"join_household_id": hh_id, "join_is_leader": False})

    await _complete_join(update, ctx, name="Thomas", rent_cents=90000, venmo=None)

    text = update.message.reply_text.call_args.args[0]
    assert "Thomas" in text
    assert "900" in text


# ── /cancel fallback ─────────────────────────────────────────────────────────

async def test_cancel_ends_conversation():
    update = _make_update()
    ctx = _make_context()

    state = await _cancel(update, ctx)

    assert state == ConversationHandler.END
    update.message.reply_text.assert_called_once()
