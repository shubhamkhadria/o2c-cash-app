"""
extract_remittances.py  -  Week 6: turn remittance documents into structured data with AI.

Run it with:   python extract_remittances.py

USE_STUB = True  -> returns prepared answers from fixtures/stub_extractions.json (free, repeatable)
USE_STUB = False -> calls the Claude API (needs the ANTHROPIC_API_KEY secret and `pip install anthropic`)
"""

import json
import os
import re
import sqlite3

import pandas as pd

USE_STUB = True
MODEL = "claude-haiku-5-5"
TOLERANCE = 1.00

# ---------------------------------------------------------------
# Step 1: the instructions we give the AI (the prompt) and the output we expect
# ---------------------------------------------------------------
PROMPT = """You extract data from customer remittance advices for an accounts receivable team.
Return ONLY valid JSON, no other text, in exactly this shape:
{
  "payer_name": string as written,
  "payment_date": "YYYY-MM-DD" or null,
  "payment_amount": number,
  "invoices": [
    {"invoice_number": string, "amount_paid": number,
     "deduction_amount": number (0 if none), "deduction_reason": string or null}
  ]
}
Rules: copy invoice numbers exactly; amounts as plain numbers without $ or commas;
if something is not in the document use null; never guess an invoice number."""


# ---------------------------------------------------------------
# Step 2: call the AI (or the stand-in)
# ---------------------------------------------------------------
def call_ai(filename, text):
    if USE_STUB:
        with open("fixtures/stub_extractions.json") as f:
            return json.load(f)[filename]

    """import anthropic                                  # only needed for the real call
    client = anthropic.Anthropic()                    # reads ANTHROPIC_API_KEY from the environment
    response = client.messages.create(
        model=MODEL, max_tokens=1000, system=PROMPT,
        messages=[{"role": "user", "content": text}],
    )
    return json.loads(response.content[0].text)"""


# ---------------------------------------------------------------
# Step 3: never trust AI output blindly - check it against our own data
# ---------------------------------------------------------------
con = sqlite3.connect("o2c.db")
open_items = pd.read_sql("""SELECT o.invoice_id, o.open_amount, c.name AS customer_name
                            FROM open_items o JOIN customers c USING (customer_id)""", con).set_index("invoice_id")
payments = pd.read_sql("SELECT bank_ref, amount, payment_date FROM bank_payments", con)


def simple_name(name):
    """'GRANITE CHEMICALS INC.' and 'Granite Chemicals Co.' -> 'GRANITE CHEMICALS'"""
    words = re.sub(r"[^A-Z ]", "", name.upper()).split()
    return " ".join(w for w in words if w not in {"INC", "LLC", "CO", "CORP"})


def validate(ext):
    issues = []
    lines = ext["invoices"]
    if abs(sum(l["amount_paid"] for l in lines) - ext["payment_amount"]) > 0.01:
        issues.append("invoice lines do not add up to the payment")
    for l in lines:
        inv = l["invoice_number"]
        if inv not in open_items.index:
            issues.append(f"{inv} is not an open invoice")
            continue
        if abs(l["amount_paid"] + l["deduction_amount"] - open_items.loc[inv, "open_amount"]) > TOLERANCE:
            issues.append(f"{inv}: paid + deduction does not equal the open amount")
        if simple_name(open_items.loc[inv, "customer_name"]) != simple_name(ext["payer_name"]):
            issues.append(f"{inv} belongs to {open_items.loc[inv, 'customer_name']}, not the payer")
        if l["deduction_amount"] > 0 and not l["deduction_reason"]:
            issues.append(f"{inv}: deduction of {l['deduction_amount']:.2f} has no reason")
    return issues


def link_to_payment(ext):
    """Find the bank payment with the same amount (and date, if several share the amount)."""
    same = payments[(payments["amount"] - ext["payment_amount"]).abs() <= 0.01]
    if len(same) > 1 and ext["payment_date"]:
        same = same[same["payment_date"] == ext["payment_date"]]
    return same["bank_ref"].iloc[0] if len(same) == 1 else None


# ---------------------------------------------------------------
# Step 4: process every document and save the results
# ---------------------------------------------------------------
rows = []
folder = "data/remittances"
for filename in sorted(os.listdir(folder)):
    with open(os.path.join(folder, filename)) as f:
        ext = call_ai(filename, f.read())
    issues = validate(ext)
    bank_ref = link_to_payment(ext)
    if bank_ref is None:
        issues.append("no single bank payment with this amount")
    status = "Validated" if not issues else "Needs review"
    for l in ext["invoices"]:
        rows.append({"source_file": filename, "bank_ref": bank_ref, "payer_name": ext["payer_name"],
                     "invoice_id": l["invoice_number"], "amount_paid": l["amount_paid"],
                     "deduction_amount": l["deduction_amount"], "deduction_reason": l["deduction_reason"],
                     "validation_status": status, "issues": "; ".join(issues)})
    print(f"{filename}  {status:<12} {'; '.join(issues)}")

pd.DataFrame(rows).to_sql("remittance_extractions", con, if_exists="replace", index=False)
con.close()
validated = sum(1 for r in rows if r["validation_status"] == "Validated")
print(f"\nSaved {len(rows)} invoice lines from {len(os.listdir(folder))} documents; {validated} lines validated")