import sqlite3

import pandas as pd

# Step 1: read the raw file
raw = pd.read_csv("data/bank_statement.csv", skiprows=4, dtype=str, keep_default_na=False) # hardcoded 4 for the timebeing
# print(f"Step 1  read {len(raw)} lines")
# print(raw.head())

# Step 2: tidy column names and trim stray spaces
df = raw.rename(columns={
    "Post Date": "payment_date",
    "Transaction Type": "txn_type",
    "Description": "description",
    "Bank Reference": "bank_ref",
    "Customer Reference": "remittance_text",
    "Credit": "credit",
    "Debit": "debit",
})
df = df.apply(lambda col: col.str.strip())
# print(df.columns.tolist())
# print(df["description"].head(3).tolist())

# Step 3: turn text into real dates and numbers
df["payment_date"] = pd.to_datetime(df["payment_date"], format="%m/%d/%Y").dt.strftime("%Y-%m-%d")


def to_number(text):
    """'$12,345.67' -> 12345.67 ; '' -> 0.0"""
    text = text.replace("$", "").replace(",", "")
    return float(text) if text else 0.0


df["credit"] = df["credit"].map(to_number)
df["debit"] = df["debit"].map(to_number)
# print(f"Step 3  total credits ${df['credit'].sum():,.2f}, total debits ${df['debit'].sum():,.2f}")

# Step 4: keep only customer receipts, drop duplicates
receipt_types = ["ACH CREDIT", "WIRE IN", "LOCKBOX DEP"]
not_receipts = df[~df["txn_type"].isin(receipt_types)]
df = df[df["txn_type"].isin(receipt_types)]

before = len(df)
df = df.drop_duplicates(subset="bank_ref")
# print(f"Step 4  removed {len(not_receipts)} non-receipt lines and {before - len(df)} duplicate(s)")

# Step 5: pull out the payer name and any invoice numbers
df["payer_name"] = df.apply(lambda r: r["description"].replace(r["txn_type"], "", 1).strip(), axis=1)
df["invoice_refs"] = df["remittance_text"].str.findall(r"9000\d{4}").str.join(",")
df["po_refs"] = df["remittance_text"].str.findall(r"PO \d+").str.join(",")
# print(df[["payer_name", "remittance_text", "invoice_refs"]].head(8).to_string(index=False))

# Step 6: save to the database and report
bank_payments = df[["bank_ref", "payment_date", "credit", "payer_name",
                    "remittance_text", "invoice_refs","po_refs"]].rename(columns={"credit": "amount"})

con = sqlite3.connect("o2c.db")
bank_payments.to_sql("bank_payments", con, if_exists="replace", index=False)
con.close()

no_ref = (bank_payments["invoice_refs"] == "").sum()
print(f"Step 6  saved {len(bank_payments)} customer payments, "
      f"${bank_payments['amount'].sum():,.2f} total; {no_ref} have no invoice number in the remittance")