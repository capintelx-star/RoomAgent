"""Tests for the debt simplification algorithm — no external services needed."""
import pytest
from utils.splits import simplify_debts, compute_balances


# --- simplify_debts ---

def test_two_person_simple():
    # Alice (1) owes Bob (2) $10
    balances = {1: -1000, 2: 1000}
    txns = simplify_debts(balances)
    assert txns == [(1, 2, 1000)]

def test_balanced_no_transactions():
    balances = {1: 0, 2: 0}
    txns = simplify_debts(balances)
    assert txns == []

def test_empty_balances():
    assert simplify_debts({}) == []

def test_three_person_two_debtors():
    # Alice (1) and Carol (3) each owe Bob (2) $500
    balances = {1: -500, 2: 1000, 3: -500}
    txns = simplify_debts(balances)
    assert len(txns) == 2
    assert sum(t[2] for t in txns) == 1000
    payees = {t[1] for t in txns}
    assert payees == {2}  # both pay Bob

def test_minimizes_transactions():
    # Three people owe one person — should be 3 transactions, not more
    balances = {1: 3000, 2: -1000, 3: -1000, 4: -1000}
    txns = simplify_debts(balances)
    assert len(txns) == 3
    assert sum(t[2] for t in txns) == 3000

def test_complex_graph():
    # Mixed creditors and debtors
    balances = {1: -300, 2: 500, 3: -200}
    txns = simplify_debts(balances)
    # Verify all debts settle to zero
    net = {uid: bal for uid, bal in balances.items()}
    for payer, payee, amt in txns:
        net[payer] += amt
        net[payee] -= amt
    assert all(v == 0 for v in net.values())


# --- compute_balances ---

def test_single_purchase_two_users():
    # Alice (1) buys $10 of TP; Bob (2) owes her $5
    purchases = [{"buyer_id": 1, "amount_cents": 1000}]
    bill_shares = []
    result = compute_balances(purchases, bill_shares, user_ids=[1, 2])
    assert result[1] == 500   # Alice is owed $5
    assert result[2] == -500  # Bob owes $5

def test_no_purchases_zero_balances():
    result = compute_balances([], [], user_ids=[1, 2])
    assert result == {1: 0, 2: 0}

def test_bill_share_reduces_balance():
    # Bob (2) owes Alice (1) $4; Alice paid the bill
    bill_shares = [{"user_id": 2, "amount_cents": 400, "paid": 0, "paid_by_user_id": 1}]
    result = compute_balances([], bill_shares, user_ids=[1, 2])
    assert result[2] == -400  # Bob owes $4
    assert result[1] == 400   # Alice is owed $4 — balances must sum to zero

def test_bill_share_balances_sum_to_zero():
    # Three-way utility split: Alice paid $90 electric, Bob and Carol each owe $30
    bill_shares = [
        {"user_id": 2, "amount_cents": 3000, "paid": 0, "paid_by_user_id": 1},
        {"user_id": 3, "amount_cents": 3000, "paid": 0, "paid_by_user_id": 1},
    ]
    result = compute_balances([], bill_shares, user_ids=[1, 2, 3])
    assert result[1] == 6000   # Alice owed $60
    assert result[2] == -3000  # Bob owes $30
    assert result[3] == -3000  # Carol owes $30
    assert sum(result.values()) == 0

def test_paid_bill_share_ignored():
    # paid=1 means already settled; should not affect balance
    bill_shares = [{"user_id": 2, "amount_cents": 400, "paid": 1, "paid_by_user_id": 1}]
    result = compute_balances([], bill_shares, user_ids=[1, 2])
    assert result == {1: 0, 2: 0}
