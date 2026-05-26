"""
Debt simplification algorithm.

Given a set of net balances (positive = owed money, negative = owes money),
produces the minimum number of transactions to settle all debts.

This is the classic "min cash flow" greedy approach: repeatedly pair the
person who owes the most with the person who is owed the most.
"""


def simplify_debts(balances: dict[int, int]) -> list[tuple[int, int, int]]:
    """
    Args:
        balances: {user_id: net_balance_cents}
                  Positive means others owe them; negative means they owe others.

    Returns:
        List of (payer_id, payee_id, amount_cents) — each a single payment
        that, when all executed, settles every debt.
    """
    # Split into creditors (owed money) and debtors (owe money).
    # Work with positive amounts throughout.
    creditors: list[list[int]] = [[uid, amt] for uid, amt in balances.items() if amt > 0]
    debtors: list[list[int]] = [[uid, -amt] for uid, amt in balances.items() if amt < 0]

    transactions: list[tuple[int, int, int]] = []
    i = j = 0

    while i < len(debtors) and j < len(creditors):
        debtor_id, owes = debtors[i]
        creditor_id, owed = creditors[j]

        payment = min(owes, owed)
        transactions.append((debtor_id, creditor_id, payment))

        debtors[i][1] -= payment
        creditors[j][1] -= payment

        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1

    return transactions


def compute_balances(
    purchases: list[dict],
    bill_shares: list[dict],
    user_ids: list[int],
) -> dict[int, int]:
    """
    Compute net balance for each user from purchase and bill share records.

    A purchase means the buyer is owed their share back from everyone else.
    A bill share means that user owes the payer.

    Returns {user_id: net_cents} — positive = owed, negative = owes.
    """
    balances: dict[int, int] = {uid: 0 for uid in user_ids}
    n = len(user_ids)
    if n == 0:
        return balances

    # Each purchase: buyer paid for everyone equally, so they're owed (n-1)/n of the total.
    for p in purchases:
        buyer = p["buyer_id"]
        total = p["amount_cents"]
        per_person = total // n
        if buyer in balances:
            balances[buyer] += total - per_person  # they keep their own share
        for uid in user_ids:
            if uid != buyer:
                balances[uid] -= per_person

    # Bill shares: debit the owing user AND credit the payer.
    # Each share record has user_id (who owes) and paid_by_user_id (who is owed).
    for share in bill_shares:
        uid = share["user_id"]
        amount = share["amount_cents"]
        paid = share["paid"]
        payer_id = share.get("paid_by_user_id")
        if not paid:
            if uid in balances:
                balances[uid] -= amount
            if payer_id and payer_id in balances:
                balances[payer_id] += amount

    return balances
