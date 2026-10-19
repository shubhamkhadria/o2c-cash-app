"""
identify_payer.py  -  Week 7: work out which customer made each payment.

Run it with:   python identify_payer.py

Order of evidence (strongest first):
  1. Account  - the payer's bank account fingerprint is on file (master data or learned)
  2. Name     - unknown account, so compare the bank's payer name with customer names
  3. None     - nothing reliable; the payment is unidentified
Writes one row per candidate customer to the payer_identification table.
"""

import re
import sqlite3
from difflib import SequenceMatcher

import pandas as pd

NAME_MATCH_THRESHOLD = 0.90   # how similar a name must be (0 to 1) to count as a match

# ---------------------------------------------------------------
# Step 1: load payments, their payer accounts, and the customer master data
# ---------------------------------------------------------------
con = sqlite3.connect("o2c.db")
payments = pd.read_sql("""SELECT b.bank_ref, b.payer_name, p.account_fingerprint, p.account_last4
                          FROM bank_payments b LEFT JOIN payment_payer_accounts p USING (bank_ref)""", con)
# LEFT JOIN: a payment with no payer account on file is still identified, by name
accounts = pd.read_sql("SELECT account_fingerprint, customer_id, source FROM customer_bank_accounts", con)
customers = pd.read_sql("SELECT customer_id, name FROM customers", con)


# ---------------------------------------------------------------
# Step 2: name matching helpers
# ---------------------------------------------------------------
def simple_name(name):
    """'HORIZON RETAIL GROUP LLC' and 'Horizon Retail Group LLC' -> 'HORIZON RETAIL GROUP'"""
    words = re.sub(r"[^A-Z ]", "", name.upper()).split()
    return " ".join(w for w in words if w not in {"INC", "LLC", "CO", "CORP"})


def name_score(bank_name, customer_name):
    """Similarity from 0 to 1. Banks often cut names short, so a clear prefix counts as a full match."""
    a, b = simple_name(bank_name), simple_name(customer_name)
    if len(a) >= 10 and b.startswith(a):
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------
# Step 3: identify the payer of one payment
# ---------------------------------------------------------------
def identify(payment):
    rows = []
    on_file = accounts[accounts["account_fingerprint"] == payment["account_fingerprint"]]
    if len(on_file):                                                   # 1. account lookup
        for cid in on_file["customer_id"].unique():
            source = on_file[on_file["customer_id"] == cid]["source"].iloc[0]
            rows.append({"customer_id": cid, "method": f"Account ({source})", "score": 1.0})
        return rows

    scores = customers.assign(score=customers["name"].map(lambda n: name_score(payment["payer_name"], n)))
    best = scores[scores["score"] >= NAME_MATCH_THRESHOLD]
    for c in best.itertuples():                                        # 2. name match
        rows.append({"customer_id": c.customer_id, "method": "Name", "score": round(c.score, 2)})
    return rows or [{"customer_id": None, "method": "Unidentified", "score": 0.0}]   # 3. nothing


# ---------------------------------------------------------------
# Step 4: run every payment and save one row per candidate customer
# ---------------------------------------------------------------
results = []
for p in payments.itertuples():
    for r in identify(p._asdict()):
        results.append({"bank_ref": p.bank_ref, "account_last4": p.account_last4, **r})
ident = pd.DataFrame(results)
ident.to_sql("payer_identification", con, if_exists="replace", index=False)

# ---------------------------------------------------------------
# Step 5: summary, and a check against the answer key
# ---------------------------------------------------------------
truth = pd.read_sql("""SELECT DISTINCT k.bank_ref, i.customer_id FROM bank_answer_key k
                       JOIN invoices i USING (invoice_id)""", con).set_index("bank_ref")["customer_id"]
con.close()

summary = []
for ref, group in ident.groupby("bank_ref"):
    candidates = set(group["customer_id"].dropna())
    actual = truth.get(ref)
    if not candidates:
        outcome = "Unidentified" + (" (correct: not a customer)" if actual is None else "")
    elif actual in candidates:
        outcome = "Correct, one customer" if len(candidates) == 1 else "Correct, within a group"
    else:
        outcome = "WRONG customer"
    summary.append({"method": group["method"].iloc[0].split(" (")[0], "outcome": outcome})

print(pd.DataFrame(summary).value_counts().sort_index().to_string())