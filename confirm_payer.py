"""
confirm_payer.py  -  Week 7: a person confirms who paid, and the system learns the bank account.

Run it with:   python confirm_payer.py <bank_ref> <customer_id>
Example:       python confirm_payer.py 3744112455 100022
"""

import sqlite3
import sys
from datetime import date

if len(sys.argv) != 3:
    raise SystemExit("Usage: python confirm_payer.py <bank_ref> <customer_id>")
bank_ref, customer_id = sys.argv[1], sys.argv[2]

con = sqlite3.connect("o2c.db")
payment = con.execute("""SELECT routing_number, account_fingerprint, account_last4
                         FROM payment_payer_accounts WHERE bank_ref = ?""", (bank_ref,)).fetchone()
if payment is None:
    raise SystemExit(f"No payment with bank reference {bank_ref}")
if con.execute("SELECT 1 FROM customers WHERE customer_id = ?", (customer_id,)).fetchone() is None:
    raise SystemExit(f"No customer {customer_id}")

routing, fingerprint, last4 = payment
already = con.execute("""SELECT 1 FROM customer_bank_accounts
                         WHERE account_fingerprint = ? AND customer_id = ?""", (fingerprint, customer_id)).fetchone()
if already:
    print(f"Account ****{last4} is already on file for customer {customer_id}; nothing to learn")
else:
    con.execute("""INSERT INTO customer_bank_accounts
                   (routing_number, account_fingerprint, account_last4, customer_id, source, confirmed_on)
                   VALUES (?, ?, ?, ?, 'learned', ?)""",
                (routing, fingerprint, last4, customer_id, date.today().isoformat()))
    con.commit()
    print(f"Learned: account ****{last4} belongs to customer {customer_id}")
con.close()