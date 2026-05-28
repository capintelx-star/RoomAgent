"""SQLite connection and schema initialization."""
import sqlite3
from pathlib import Path

from config import DB_PATH

_MIGRATIONS_DIR = Path(__file__).parent.parent / "migrations"


def get_conn() -> sqlite3.Connection:
    """Return a connection with row_factory and foreign keys enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    return any(r["name"] == column for r in rows)


def init_db() -> None:
    """Run schema migrations in order. Each migration is idempotent."""
    with get_conn() as conn:
        conn.executescript((_MIGRATIONS_DIR / "001_init.sql").read_text())

    # Migration 002: chore tables rebuilt with simplified tracking-only schema.
    # Guard: old schema has assigned_to_user_id; new schema uses user_id only.
    with get_conn() as conn:
        if _column_exists(conn, "chore_completions", "assigned_to_user_id"):
            conn.executescript((_MIGRATIONS_DIR / "002_chores.sql").read_text())

    # Migration 003: settled_at on actions for settle-up reset.
    with get_conn() as conn:
        if not _column_exists(conn, "actions", "settled_at"):
            conn.executescript((_MIGRATIONS_DIR / "003_settle.sql").read_text())

    # Migration 004: per-user rent amount, leader flag, and onboarding columns on households.
    with get_conn() as conn:
        if not _column_exists(conn, "users", "rent_amount_cents"):
            conn.executescript((_MIGRATIONS_DIR / "004_onboarding.sql").read_text())


# --- Lookup helpers used across multiple handlers ---

def get_household(chat_id: int) -> sqlite3.Row | None:
    """Return the household row for a Telegram chat_id, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM households WHERE telegram_chat_id = ?", (chat_id,)
        ).fetchone()


def get_user(household_id: int, telegram_user_id: int) -> sqlite3.Row | None:
    """Return the user row for a given household + Telegram user id, or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE household_id = ? AND telegram_user_id = ?",
            (household_id, telegram_user_id),
        ).fetchone()


def get_household_users(household_id: int) -> list[sqlite3.Row]:
    """Return all user rows for a household."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE household_id = ?", (household_id,)
        ).fetchall()


def compute_household_balances(household_id: int) -> dict[int, int]:
    """
    Compute all-time net balance for every user in the household.

    Positive = others owe them. Negative = they owe others.
    Balances always sum to zero (no money created or destroyed).

    Sources:
      - purchases: buyer is credited (n-1)/n of cost; others debited 1/n each
      - bill_shares (unpaid): owing user debited, bill payer credited
    """
    with get_conn() as conn:
        user_ids = [
            r["id"]
            for r in conn.execute(
                "SELECT id FROM users WHERE household_id = ?", (household_id,)
            ).fetchall()
        ]
        n = len(user_ids)
        if n == 0:
            return {}

        balances: dict[int, int] = {uid: 0 for uid in user_ids}

        # Purchases: equal split, buyer keeps their share.
        # Exclude purchases whose action has been settled (/settle confirm).
        for p in conn.execute(
            "SELECT p.buyer_id, p.amount_cents FROM purchases p "
            "WHERE p.household_id = ? "
            "AND p.id NOT IN ("
            "  SELECT json_extract(a.payload_json, '$.purchase_id') FROM actions a "
            "  WHERE a.household_id = ? AND a.action_type = 'purchase_add' "
            "  AND a.settled_at IS NOT NULL AND a.reversed_at IS NULL"
            ")",
            (household_id, household_id),
        ).fetchall():
            per_person = p["amount_cents"] // n
            if p["buyer_id"] in balances:
                balances[p["buyer_id"]] += p["amount_cents"] - per_person
            for uid in user_ids:
                if uid != p["buyer_id"]:
                    balances[uid] -= per_person

        # Bill shares: debit the owing user, credit the payer.
        # Exclude bills whose action has been settled.
        for share in conn.execute(
            "SELECT bs.user_id, bs.amount_cents, b.paid_by_user_id "
            "FROM bill_shares bs "
            "JOIN bills b ON b.id = bs.bill_id "
            "WHERE b.household_id = ? AND bs.paid = 0 "
            "AND b.id NOT IN ("
            "  SELECT json_extract(a.payload_json, '$.bill_id') FROM actions a "
            "  WHERE a.household_id = ? AND a.action_type = 'bill_add' "
            "  AND a.settled_at IS NOT NULL AND a.reversed_at IS NULL"
            ")",
            (household_id, household_id),
        ).fetchall():
            if share["user_id"] in balances:
                balances[share["user_id"]] -= share["amount_cents"]
            if share["paid_by_user_id"] in balances:
                balances[share["paid_by_user_id"]] += share["amount_cents"]

        return balances


def settle_actions(household_id: int) -> int:
    """
    Stamp settled_at on all active purchase_add and bill_add actions.
    Returns the number of actions marked. Called by /settle confirm.
    """
    with get_conn() as conn:
        result = conn.execute(
            "UPDATE actions SET settled_at = datetime('now') "
            "WHERE household_id = ? AND settled_at IS NULL AND reversed_at IS NULL "
            "AND action_type IN ('purchase_add', 'bill_add')",
            (household_id,),
        )
        return result.rowcount
