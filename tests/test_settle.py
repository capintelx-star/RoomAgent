"""Tests for settle-up: balance filtering after settle, settle_actions, Venmo links."""
import json
import sqlite3

import pytest

import db as db_module
from db import _column_exists, compute_household_balances, get_conn, init_db, settle_actions
from handlers.settle import _venmo_link


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    test_db = str(tmp_path / "test.db")
    original = db_module.DB_PATH
    db_module.DB_PATH = test_db
    init_db()
    yield
    db_module.DB_PATH = original


def _seed(conn: sqlite3.Connection) -> tuple[int, int, int]:
    """Insert household + two users. Returns (household_id, user1_id, user2_id)."""
    hh = conn.execute(
        "INSERT INTO households (telegram_chat_id, name) VALUES (1, 'Test')"
    ).lastrowid
    u1 = conn.execute(
        "INSERT INTO users (household_id, telegram_user_id, name, venmo_handle) "
        "VALUES (?, 101, 'Alice', '@alice_v')", (hh,)
    ).lastrowid
    u2 = conn.execute(
        "INSERT INTO users (household_id, telegram_user_id, name) VALUES (?, 102, 'Bob')", (hh,)
    ).lastrowid
    return hh, u1, u2


# --- Migration guard ---

def test_settled_at_column_added():
    with get_conn() as conn:
        assert _column_exists(conn, "actions", "settled_at")


# --- settle_actions ---

def test_settle_actions_stamps_purchase_and_bill_actions():
    with get_conn() as conn:
        hh, u1, u2 = _seed(conn)
        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'purchase_add', ?)", (hh, u1, json.dumps({"purchase_id": 1}))
        )
        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'bill_add', ?)", (hh, u1, json.dumps({"bill_id": 1}))
        )

    count = settle_actions(hh)
    assert count == 2

    with get_conn() as conn:
        unsettled = conn.execute(
            "SELECT COUNT(*) FROM actions WHERE household_id = ? AND settled_at IS NULL", (hh,)
        ).fetchone()[0]
        assert unsettled == 0


def test_settle_actions_skips_chore_and_join_actions():
    with get_conn() as conn:
        hh, u1, u2 = _seed(conn)
        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'chore_done', ?)", (hh, u1, json.dumps({"completion_id": 1}))
        )
        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
            "VALUES (?, ?, 'user_join', ?)", (hh, u1, json.dumps({"user_id": u1}))
        )

    count = settle_actions(hh)
    assert count == 0


def test_settle_actions_not_re_settled():
    with get_conn() as conn:
        hh, u1, _ = _seed(conn)
        conn.execute(
            "INSERT INTO actions (household_id, user_id, action_type, payload_json, settled_at) "
            "VALUES (?, ?, 'purchase_add', ?, datetime('now'))",
            (hh, u1, json.dumps({"purchase_id": 1}))
        )

    count = settle_actions(hh)
    assert count == 0  # already settled


def test_settle_actions_returns_zero_when_nothing_to_settle():
    with get_conn() as conn:
        hh, u1, _ = _seed(conn)
    assert settle_actions(hh) == 0


# --- compute_household_balances after settling ---

def _log_purchase(conn, hh, buyer_id, amount_cents):
    """Insert a purchase + action log. Returns purchase_id."""
    pid = conn.execute(
        "INSERT INTO purchases (household_id, buyer_id, item, amount_cents) "
        "VALUES (?, ?, 'thing', ?)", (hh, buyer_id, amount_cents)
    ).lastrowid
    conn.execute(
        "INSERT INTO actions (household_id, user_id, action_type, payload_json) "
        "VALUES (?, ?, 'purchase_add', ?)",
        (hh, buyer_id, json.dumps({"purchase_id": pid}))
    )
    return pid


def test_unsettled_purchase_included_in_balance():
    with get_conn() as conn:
        hh, u1, u2 = _seed(conn)
        _log_purchase(conn, hh, u1, 1000)  # Alice paid $10

    balances = compute_household_balances(hh)
    # Alice is owed $5 (her share back from Bob), Bob owes $5
    assert balances[u1] == 500
    assert balances[u2] == -500


def test_settled_purchase_excluded_from_balance():
    with get_conn() as conn:
        hh, u1, u2 = _seed(conn)
        _log_purchase(conn, hh, u1, 1000)

    settle_actions(hh)
    balances = compute_household_balances(hh)
    assert balances[u1] == 0
    assert balances[u2] == 0


def test_post_settle_new_purchase_still_counted():
    with get_conn() as conn:
        hh, u1, u2 = _seed(conn)
        _log_purchase(conn, hh, u1, 1000)

    settle_actions(hh)

    with get_conn() as conn:
        _log_purchase(conn, hh, u2, 600)  # Bob paid $6 after settling

    balances = compute_household_balances(hh)
    # Only the new $6 purchase counts. Bob paid $6, Alice owes $3, Bob nets +$3
    assert balances[u2] == 300
    assert balances[u1] == -300


def test_reversed_purchase_never_counted():
    with get_conn() as conn:
        hh, u1, u2 = _seed(conn)
        pid = _log_purchase(conn, hh, u1, 1000)
        conn.execute(
            "DELETE FROM purchases WHERE id = ?", (pid,)
        )
        conn.execute(
            "UPDATE actions SET reversed_at = datetime('now') WHERE action_type = 'purchase_add'"
        )

    balances = compute_household_balances(hh)
    assert balances[u1] == 0
    assert balances[u2] == 0


# --- Venmo link ---

def test_venmo_link_strips_at():
    link = _venmo_link("@alice_v", 4200)
    assert "recipients=alice_v" in link
    assert "@" not in link


def test_venmo_link_no_at():
    link = _venmo_link("alice_v", 4200)
    assert "recipients=alice_v" in link


def test_venmo_link_amount_dollars():
    link = _venmo_link("alice_v", 4200)
    assert "amount=42.00" in link


def test_venmo_link_scheme():
    link = _venmo_link("x", 100)
    assert link.startswith("venmo://paycharge")


def test_venmo_link_note_encoded():
    link = _venmo_link("x", 100)
    assert "note=Roomagent" in link
    assert " " not in link  # spaces must be encoded
