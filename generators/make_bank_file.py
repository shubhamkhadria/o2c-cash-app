"""
make_bank_file.py  -  Week 4: create a realistic, messy bank statement file.

Run it with:   python make_bank_file.py

It reads your open items from o2c.db and pretends some customers paid them
between Oct 13 and Oct 15, 2026. It writes:
  data/bank_statement.csv   ~ what the bank sends you (messy, like real life)
  bank_answer_key (table)   ~ which invoices each payment really pays (for grading later)
"""

import os
import random
import sqlite3
from datetime import date, timedelta

import pandas as pd

SEED = 7
NUM_PAYMENTS = 45
STATEMENT_DAYS = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15)]
PAYMENT_TYPES = {"full": 0.60, "multi_invoice": 0.15, "partial": 0.10, "short_pay": 0.15}
REMITTANCE = {"clean": 0.45, "messy_text": 0.20, "po_number_only": 0.10, "missing": 0.15, "typo": 0.10}

random.seed(SEED)
con = sqlite3.connect("o2c.db")
open_items = pd.read_sql("""
    SELECT o.invoice_id, o.customer_id, o.open_amount, c.name
    FROM open_items o JOIN customers c USING (customer_id)
""", con)


def pick(weights):
    return random.choices(list(weights), weights=list(weights.values()))[0]


def bank_name(name):
    """Banks shorten and upper-case names in different ways."""
    style = random.choice(["full", "upper", "truncated", "no_suffix"])
    if style == "upper":
        return name.upper()
    if style == "truncated":
        return name.upper().replace(".", "")[:16]
    if style == "no_suffix":
        return " ".join(name.split()[:-1]).upper()
    return name


def remittance(invoice_ids):
    style = pick(REMITTANCE)
    if style == "clean":
        return "INV " + ",".join(invoice_ids)
    if style == "messy_text":
        return "Payment for invoice#" + " & ".join(invoice_ids) + " thank you"
    if style == "po_number_only":
        return f"PO 45{random.randint(10000000, 99999999)}"
    if style == "typo":
        return "INV " + str(int(invoice_ids[0]) + random.choice([-1, 1]))
    return ""


def money(x):
    return f"${x:,.2f}"


rows, answer_key = [], []
used = set()
by_customer = open_items.groupby("customer_id")

for _ in range(NUM_PAYMENTS):
    available = open_items[~open_items["invoice_id"].isin(used)]
    if available.empty:
        break
    inv = available.sample(1, random_state=random.randint(0, 10**6)).iloc[0]
    ptype = pick(PAYMENT_TYPES)
    group = [inv]

    if ptype == "multi_invoice":
        siblings = available[(available["customer_id"] == inv["customer_id"]) &
                             (available["invoice_id"] != inv["invoice_id"])]
        if len(siblings):
            extra = siblings.head(random.randint(1, 2))
            group += [r for _, r in extra.iterrows()]
        else:
            ptype = "full"

    for g in group:
        used.add(g["invoice_id"])

    bank_ref = f"{random.randint(10**9, 10**10 - 1)}"
    if ptype == "partial":
        applied = [round(inv["open_amount"] * random.uniform(0.3, 0.7), 2)]
    elif ptype == "short_pay":
        applied = [round(inv["open_amount"] * random.uniform(0.85, 0.98), 2)]
    else:
        applied = [round(g["open_amount"], 2) for g in group]
    amount = round(sum(applied), 2)

    channel = random.choice(["ACH CREDIT", "ACH CREDIT", "WIRE IN", "LOCKBOX DEP"])
    rows.append({
        "Post Date": random.choice(STATEMENT_DAYS).strftime("%m/%d/%Y"),
        "Transaction Type": channel,
        "Description": f"  {channel} {bank_name(inv['name'])}  ",   # stray spaces, on purpose
        "Bank Reference": bank_ref,
        "Customer Reference": remittance([g["invoice_id"] for g in group]),
        "Credit": money(amount),
        "Debit": "",
    })
    for g, a in zip(group, applied):
        answer_key.append({"bank_ref": bank_ref, "invoice_id": g["invoice_id"], "applied_amount": a})

# non-customer lines every real statement has
for d in STATEMENT_DAYS:
    rows.append({"Post Date": d.strftime("%m/%d/%Y"), "Transaction Type": "FEE",
                 "Description": "MONTHLY SERVICE FEE", "Bank Reference": f"{random.randint(10**9, 10**10 - 1)}",
                 "Customer Reference": "", "Credit": "", "Debit": money(random.choice([15, 25, 35]))})
rows.append({"Post Date": STATEMENT_DAYS[-1].strftime("%m/%d/%Y"), "Transaction Type": "INTEREST",
             "Description": "INTEREST CREDIT", "Bank Reference": f"{random.randint(10**9, 10**10 - 1)}",
             "Customer Reference": "", "Credit": money(12.48), "Debit": ""})
# a payment from someone who is not a customer
rows.append({"Post Date": STATEMENT_DAYS[1].strftime("%m/%d/%Y"), "Transaction Type": "ACH CREDIT",
             "Description": "ACH CREDIT VENDOR REFUND ACME FREIGHT", "Bank Reference": f"{random.randint(10**9, 10**10 - 1)}",
             "Customer Reference": "REFUND CM 7781", "Credit": money(842.10), "Debit": ""})
# banks occasionally send the same line twice
rows.append(dict(rows[3]))

random.shuffle(rows)
os.makedirs("data", exist_ok=True)
with open("data/bank_statement.csv", "w") as f:
    f.write("First Midwest Commercial Bank - Account Activity Export\n")
    f.write("Account: ****4821  Operating Account\n")
    f.write("Period: 10/13/2026 - 10/15/2026\n")
    f.write("\n")
    pd.DataFrame(rows).to_csv(f, index=False)

pd.DataFrame(answer_key).to_sql("bank_answer_key", con, if_exists="replace", index=False)
con.close()
print(f"Wrote data/bank_statement.csv with {len(rows)} lines "
      f"({len(answer_key)} invoice allocations in bank_answer_key)")
