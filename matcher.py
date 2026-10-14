"""
matcher.py  -  Week 5: rule-based cash application matcher.

Run it with:   python matcher.py
Rules are documented in docs/matching_rules.md
"""

import re
import sqlite3

import pandas as pd

# Settings (from docs/matching_rules.md)
TOLERANCE = 1.00          # dollars
SHORT_PAY_THRESHOLD = 0.80

# ---------------------------------------------------------------
# Step 1: load payments and open invoices
# ---------------------------------------------------------------
con = sqlite3.connect("o2c.db")
payments = pd.read_sql("SELECT * FROM bank_payments", con)
open_items = pd.read_sql("SELECT invoice_id, customer_id, due_date, open_amount FROM open_items", con)
open_by_id = open_items.set_index("invoice_id")
print(f"Step 1  {len(payments)} payments, {len(open_items)} open invoices")


# ---------------------------------------------------------------
# Step 2: find valid invoice references in the remittance text
# ---------------------------------------------------------------
def valid_refs(text):
    """Any 6-10 digit number that is a real open invoice, in the order written."""
    found = []
    for number in re.findall(r"\d{6,10}", text):
        if number in open_by_id.index and number not in found:
            found.append(number)
    return found


# ---------------------------------------------------------------
# Step 3: apply the rules to one payment
# ---------------------------------------------------------------
def decide(payment):
    """Return (matches, residuals) for one payment, following rules R1-R7."""
    ref, amount = payment["bank_ref"], payment["amount"]
    refs = valid_refs(payment["remittance_text"])

    def match(invoice_id, applied, rule, status):
        return {"bank_ref": ref, "invoice_id": invoice_id, "applied_amount": round(applied, 2),
                "rule": rule, "status": status}

    def residual(invoice_id, amt, rtype, reason):
        inv = open_by_id.loc[invoice_id]
        return {"bank_ref": ref, "original_invoice_id": invoice_id, "customer_id": inv["customer_id"],
                "amount": round(amt, 2), "residual_type": rtype, "reason": reason,
                "due_date": inv["due_date"]}

    if refs:
        invoices = open_by_id.loc[refs]
        total = invoices["open_amount"].sum()
        diff = amount - total

        if abs(diff) <= TOLERANCE:                                   # R1 exact
            return [match(i, invoices.loc[i, "open_amount"], "R1 Exact", "Auto") for i in refs], []
        if diff > TOLERANCE:                                         # R5 overpayment
            return [match(i, invoices.loc[i, "open_amount"], "R5 Overpayment", "Review") for i in refs], []
        if len(refs) > 1:                                            # R4 multi-invoice short
            return [match(i, invoices.loc[i, "open_amount"], "R4 Multi-invoice short", "Review") for i in refs], []

        inv_id = refs[0]
        short = total - amount
        if amount >= SHORT_PAY_THRESHOLD * total:                    # R2 deduction
            return ([match(inv_id, amount, "R2 Deduction", "Auto")],
                    [residual(inv_id, short, "Deduction", "Short pay - to investigate")])
        return ([match(inv_id, amount, "R3 Partial", "Auto")],     # R3 partial
                [residual(inv_id, short, "Balance due", "Partial payment")])

    same_amount = open_items[(open_items["open_amount"] - amount).abs() <= TOLERANCE]
    if len(same_amount) == 1:                                        # R6 amount only
        inv_id = same_amount.iloc[0]["invoice_id"]
        return [match(inv_id, amount, "R6 Amount only", "Review")], []

    return [match(None, 0.0, "R7 No match", "Exception")], []        # R7


# ---------------------------------------------------------------
# Step 4: run every payment through the rules and save the proposals
# ---------------------------------------------------------------
all_matches, all_residuals = [], []
for _, payment in payments.iterrows():
    matches, residuals = decide(payment)
    all_matches += matches
    all_residuals += residuals

match_results = pd.DataFrame(all_matches)
proposed_residuals = pd.DataFrame(all_residuals,
                                  columns=["bank_ref", "original_invoice_id", "customer_id", "amount",
                                           "residual_type", "reason", "due_date"])
match_results.to_sql("match_results", con, if_exists="replace", index=False)
proposed_residuals.to_sql("proposed_residuals", con, if_exists="replace", index=False)
con.close()

# ---------------------------------------------------------------
# Step 5: summary, one line per payment outcome
# ---------------------------------------------------------------
per_payment = match_results.drop_duplicates("bank_ref")
print("Step 5  outcomes by rule (payments):")
print(per_payment["rule"].value_counts().sort_index().to_string())
print(f"Residual items proposed: {len(proposed_residuals)} "
      f"(${proposed_residuals['amount'].sum():,.2f})")