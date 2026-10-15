"""
load_bank_accounts.py  -  Week 7: load bank accounts into the database WITHOUT storing the raw numbers.

Run it with:   python load_bank_accounts.py
Needs the BANK_HASH_KEY Codespaces secret.
"""

import hashlib
import hmac
import os
import sqlite3

import pandas as pd

# ---------------------------------------------------------------
# Step 1: get the secret key, and stop with a clear message if it is missing
# ---------------------------------------------------------------
KEY = os.environ.get("BANK_HASH_KEY")
if not KEY:
    raise SystemExit("BANK_HASH_KEY is not set. Add it as a Codespaces secret, then restart the codespace.")


# ---------------------------------------------------------------
# Step 2: the fingerprint - same account in, same code out; the code cannot be turned back into the account
# ---------------------------------------------------------------
def fingerprint(routing, account):
    return hmac.new(KEY.encode(), f"{routing}:{account}".encode(), hashlib.sha256).hexdigest()


def protect(df):
    """Replace raw account numbers with a fingerprint and the last 4 digits."""
    df["account_fingerprint"] = df.apply(lambda r: fingerprint(r["routing_number"], r["account_number"]), axis=1)
    df["account_last4"] = df["account_number"].str[-4:]
    return df.drop(columns=["account_number"])               # the raw number goes no further


# ---------------------------------------------------------------
# Step 3: customer bank accounts (master data, like SAP KNBK)
# ---------------------------------------------------------------
master = protect(pd.read_csv("data/onboarding_bank_accounts.csv", dtype=str))
master["source"] = "master_data"
master["confirmed_on"] = "2026-10-12"

con = sqlite3.connect("o2c.db")
con.execute("""CREATE TABLE IF NOT EXISTS customer_bank_accounts (
    routing_number TEXT, account_fingerprint TEXT, account_last4 TEXT,
    customer_id TEXT, source TEXT, confirmed_on TEXT)""")
con.execute("DELETE FROM customer_bank_accounts WHERE source = 'master_data'")   # keep learned rows
master[["routing_number", "account_fingerprint", "account_last4", "customer_id", "source", "confirmed_on"]] \
    .to_sql("customer_bank_accounts", con, if_exists="append", index=False)

# ---------------------------------------------------------------
# Step 4: which account each bank payment came from
# ---------------------------------------------------------------
payer_accounts = protect(pd.read_csv("data/payment_payer_accounts.csv", dtype=str))
payer_accounts.to_sql("payment_payer_accounts", con, if_exists="replace", index=False)

# ---------------------------------------------------------------
# Step 5: report
# ---------------------------------------------------------------
shared = pd.read_sql("""SELECT account_fingerprint FROM customer_bank_accounts
                        GROUP BY account_fingerprint HAVING COUNT(DISTINCT customer_id) > 1""", con)
known = pd.read_sql("""SELECT COUNT(DISTINCT p.bank_ref) AS n FROM payment_payer_accounts p
                       JOIN customer_bank_accounts c USING (account_fingerprint)""", con)["n"][0]
columns = [row[1] for row in con.execute("PRAGMA table_info(customer_bank_accounts)")]
con.commit()
con.close()

print(f"Customer bank accounts: {len(master)} rows; {len(shared)} accounts shared by more than one customer")
print(f"Payments: {len(payer_accounts)}; {known} came from an account already on file")
print(f"Raw account numbers stored in the database: {'account_number' in columns}")