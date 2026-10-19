"""
pipeline/extract_remittances.py  -  turn remittance documents into structured data with AI.

Run it from the repo root:   python pipeline/extract_remittances.py

EXTRACTOR = "stub"    prepared answers from fixtures/stub_extractions.json, with deliberate misreads
                      planted in two of them so the exceptions agent has something to catch
EXTRACTOR = "ollama"  a real model reads each document: gpt-oss:20b on Ollama Cloud when the
                      OLLAMA_API_KEY Codespaces secret is set, otherwise local qwen2.5:3b
Either way, every answer is validated against the ledger (Step 3) before anything uses it.
"""

import json
import os
import re
import sqlite3

import pandas as pd

EXTRACTOR = "stub"
CLOUD_KEY = os.environ.get("OLLAMA_API_KEY")          # a Codespaces secret; never written in code
MODEL = "gpt-oss:20b" if CLOUD_KEY else "qwen2.5:3b"
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
amount_paid is the NET amount actually paid for that invoice (after any deduction), never the gross
invoice amount; the amount_paid of all lines must add up to payment_amount;
payer_name is the company name only, without a department ("Accounts Payable") or an email address;
"short X", "deducted X" or a balance to follow means deduction_amount X, with the reason given;
if something is not in the document use null; never guess an invoice number."""


# ---------------------------------------------------------------
# Step 2: call the AI (or the stand-in)
# ---------------------------------------------------------------
def call_ai(filename, text, feedback=None):
    if EXTRACTOR == "stub":
        with open("fixtures/stub_extractions.json") as f:
            return json.load(f)[filename]

    import ollama                                     # only needed for the real call
    client = (ollama.Client(host="https://ollama.com", headers={"Authorization": f"Bearer {CLOUD_KEY}"})
              if CLOUD_KEY else ollama)
    messages = [{"role": "system", "content": PROMPT}, {"role": "user", "content": text}]
    if feedback:                                      # second try: tell the model what the ledger check found
        messages.append({"role": "user", "content": "Your previous answer failed these checks against the "
                         f"ledger: {feedback}. Read the document again and correct your JSON."})
    reply = client.chat(model=MODEL, format="json", options={"temperature": 0}, messages=messages)
    return clean(json.loads(reply.message.content))


def clean(ext):
    """Models sometimes send numbers as text ("1,120.37") or leave a field out: tidy that up."""
    def num(v):
        return float(str(v).replace(",", "").replace("$", "")) if v not in (None, "") else 0.0
    ext["payment_amount"] = num(ext.get("payment_amount"))
    ext.setdefault("payer_name", "")
    ext.setdefault("payment_date", None)
    ext["invoices"] = [{"invoice_number": str(l.get("invoice_number", "")).strip(),
                        "amount_paid": num(l.get("amount_paid")),
                        "deduction_amount": num(l.get("deduction_amount")),
                        "deduction_reason": l.get("deduction_reason") or None}
                       for l in ext.get("invoices") or []]
    return ext


# ---------------------------------------------------------------
# Step 3: never trust AI output blindly - check it against our own data
# ---------------------------------------------------------------
con = sqlite3.connect("o2c.db")
open_items = pd.read_sql("""SELECT o.invoice_id, o.open_amount, c.name AS customer_name
                            FROM open_items o JOIN customers c USING (customer_id)""", con).set_index("invoice_id")
payments = pd.read_sql("SELECT bank_ref, amount, payment_date FROM bank_payments", con)


def simple_name(name):
    """'GRANITE CHEMICALS INC.' and 'Granite Chemicals Co. - Accounts Payable' -> 'GRANITE CHEMICALS'"""
    name = (name or "").split(" - ")[0]               # drop a department after a dash
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
        text = f.read()
    ext = call_ai(filename, text)
    issues = validate(ext)
    retried = False
    if issues and EXTRACTOR == "ollama":              # one retry, with the validation result as feedback
        second = call_ai(filename, text, feedback="; ".join(issues))
        if not validate(second):                      # keep the retry only if it now passes every check
            ext, issues, retried = second, [], True
    bank_ref = link_to_payment(ext)
    if bank_ref is None:
        issues.append("no single bank payment with this amount")
    status = "Validated" if not issues else "Needs review"
    for l in ext["invoices"]:
        rows.append({"source_file": filename, "bank_ref": bank_ref, "payer_name": ext["payer_name"],
                     "invoice_id": l["invoice_number"], "amount_paid": l["amount_paid"],
                     "deduction_amount": l["deduction_amount"], "deduction_reason": l["deduction_reason"],
                     "validation_status": status, "issues": "; ".join(issues)})
    print(f"{filename}  {status:<12} {'(fixed on retry) ' if retried else ''}{'; '.join(issues)}")

pd.DataFrame(rows).to_sql("remittance_extractions", con, if_exists="replace", index=False)

# ---------------------------------------------------------------
# Step 5 (test mode): how accurate was the extraction? Only possible because the synthetic data has an answer key
# ---------------------------------------------------------------
key = pd.read_sql("SELECT bank_ref, invoice_id, applied_amount FROM bank_answer_key", con)
truth = {(r.bank_ref, r.invoice_id): r.applied_amount for r in key.itertuples()}
correct = sum(1 for r in rows
              if abs(truth.get((r["bank_ref"], r["invoice_id"]), -1) - r["amount_paid"]) <= 0.01)
con.close()

validated = sum(1 for r in rows if r["validation_status"] == "Validated")
print(f"\nExtractor: {EXTRACTOR}" + (f" ({MODEL})" if EXTRACTOR == "ollama" else ""))
print(f"Saved {len(rows)} invoice lines from {len(os.listdir(folder))} documents; {validated} lines validated")
print(f"Accuracy vs answer key: {correct} of {len(rows)} lines have the right invoice AND amount")