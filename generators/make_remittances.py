"""
make_remittances.py  -  Week 6: create remittance advices that customers send separately from the payment.

Run it with:   python make_remittances.py

For payments the matcher could not auto-apply, it writes one remittance document
into data/remittances/ - in three styles (email, table, quick note), like real life.
"""

import os
import random
import sqlite3

import pandas as pd

SEED = 11
random.seed(SEED)
REASONS = ["Damaged goods", "Pricing difference", "Promotional allowance", "Freight charge disputed"]

con = sqlite3.connect("o2c.db")
targets = pd.read_sql("""
    SELECT DISTINCT m.bank_ref, b.amount, b.payment_date
    FROM match_results m JOIN bank_payments b USING (bank_ref)
    WHERE m.status != 'Auto'
      AND m.bank_ref IN (SELECT bank_ref FROM bank_answer_key)
    ORDER BY m.bank_ref
""", con)
truth = pd.read_sql("""
    SELECT k.bank_ref, k.invoice_id, k.applied_amount, o.open_amount, c.name
    FROM bank_answer_key k
    JOIN open_items o USING (invoice_id)
    JOIN customers c ON c.customer_id = o.customer_id
""", con)
con.close()


def money(x):
    return f"${x:,.2f}"


def email_style(name, date, amount, lines):
    domain = name.split()[0].lower() + ".com"
    body = [f"From: accounts.payable@{domain}",
            "Subject: Remittance advice",
            "",
            "Hello,",
            "",
            f"Please be advised we sent payment of {money(amount)} on {date}.",
            "This payment covers the following:",
            ""]
    for l in lines:
        text = f"  Invoice {l['inv']}  paid {money(l['paid'])}"
        if l["ded"] > 0:
            text += f"  (deducted {money(l['ded'])}: {l['reason']})"
        body.append(text)
    body += ["", "Regards,", f"{name} - Accounts Payable"]
    return "\n".join(body)


def table_style(name, date, amount, lines):
    body = ["REMITTANCE ADVICE",
            f"Payer: {name.upper()}",
            f"Payment date: {date}    Total: {amount:,.2f} USD",
            "",
            "Document     Gross        Deduction   Net paid    Reason",
            "-" * 64]
    for l in lines:
        gross = l["paid"] + l["ded"]
        body.append(f"{l['inv']:<12} {gross:>10,.2f}  {l['ded']:>10,.2f}  {l['paid']:>10,.2f}  "
                    f"{l['reason'] if l['ded'] > 0 else ''}")
    return "\n".join(body)


def note_style(name, date, amount, lines):
    parts = []
    for l in lines:
        p = f"inv {l['inv']} {l['paid']:.2f}"
        if l["ded"] > 0:
            p += f" (short {l['ded']:.2f} - {l['reason'].lower()})"
        parts.append(p)
    return (f"hi - {name} here. sent {amount:.2f} today ({date}). "
            f"pls apply: {'; '.join(parts)}. thx")


os.makedirs("data/remittances", exist_ok=True)
for n, t in enumerate(targets.itertuples(), start=1):
    rows = truth[truth["bank_ref"] == t.bank_ref]
    name = rows["name"].iloc[0]
    lines = []
    for r in rows.itertuples():
        short = round(r.open_amount - r.applied_amount, 2)
        if short <= 0.01:
            ded, reason = 0.0, ""
        elif r.applied_amount < 0.8 * r.open_amount:
            ded, reason = short, "Partial payment, balance to follow"
        else:
            ded, reason = short, random.choice(REASONS)
        lines.append({"inv": r.invoice_id, "paid": r.applied_amount, "ded": ded, "reason": reason})

    style = random.choice([email_style, table_style, note_style])
    text = style(name, t.payment_date, t.amount, lines)
    with open(f"data/remittances/remit_{n:02d}.txt", "w") as f:
        f.write(text + "\n")

print(f"Wrote {len(targets)} remittance documents to data/remittances/")
