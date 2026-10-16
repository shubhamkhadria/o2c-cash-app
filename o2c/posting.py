"""
o2c/posting.py  -  Week 8: turn an approved proposal into ledger entries.

This is the step where the product stops proposing and starts doing the work,
and it only happens when a person (or an Auto rule the team agreed) approves it.

  accept(bank_ref, user)   clear the invoices, create residual items, learn the account, log the decision
  reject(bank_ref, user, note)   send the payment to exceptions with a reason
  post_all_auto(user)      accept every Auto proposal that has not been decided yet
  reset_demo()             put the database back to how it was before the first posting
"""

import os
import shutil
import sqlite3
from datetime import datetime

from o2c.db import DB_PATH

SNAPSHOT = "o2c_before_posting.db"   # also ignored by .gitignore (*.db)
TOLERANCE = 0.01


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def setup():
    """Create the decision log if it does not exist yet. The app calls this when a page opens."""
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS decisions (
        bank_ref TEXT PRIMARY KEY, decision TEXT, customer_id TEXT,
        decided_by TEXT, decided_at TEXT, note TEXT)""")
    con.commit()
    con.close()


def _connect():
    setup()
    if not os.path.exists(SNAPSHOT):              # first posting ever: keep a copy so the demo can be reset
        shutil.copy(DB_PATH, SNAPSHOT)
    return sqlite3.connect(DB_PATH)


def _already_decided(con, bank_ref):
    row = con.execute("SELECT decision FROM decisions WHERE bank_ref = ?", (bank_ref,)).fetchone()
    return row[0] if row else None


def _learn_account(con, bank_ref, customer_id, today):
    """The analyst's approval tells us who owns this account. Learn it, unless it clashes."""
    acct = con.execute("""SELECT routing_number, account_fingerprint, account_last4
                          FROM payment_payer_accounts WHERE bank_ref = ?""", (bank_ref,)).fetchone()
    if acct is None:
        return "No payer account on this payment"
    routing, fp, last4 = acct
    owners = {r[0] for r in con.execute(
        "SELECT customer_id FROM customer_bank_accounts WHERE account_fingerprint = ?", (fp,))}
    if customer_id in owners:
        return f"Account ****{last4} already on file"
    if owners:                                    # conflict check: the account belongs to someone else
        return f"Account ****{last4} is on file for another customer; NOT learned, flag for master data review"
    con.execute("""INSERT INTO customer_bank_accounts
                   (routing_number, account_fingerprint, account_last4, customer_id, source, confirmed_on)
                   VALUES (?, ?, ?, ?, 'learned', ?)""", (routing, fp, last4, customer_id, today))
    return f"Learned account ****{last4} for customer {customer_id}"


# ---------------------------------------------------------------
# Accept: post one payment
# ---------------------------------------------------------------
def accept(bank_ref, user):
    con = _connect()
    try:
        if _already_decided(con, bank_ref):
            raise ValueError(f"{bank_ref} was already {_already_decided(con, bank_ref).lower()}")
        payment_date = con.execute("SELECT payment_date FROM bank_payments WHERE bank_ref = ?",
                                   (bank_ref,)).fetchone()[0]
        lines = con.execute("""SELECT m.invoice_id, m.applied_amount, i.open_amount, i.customer_id
                               FROM match_results m JOIN invoices i USING (invoice_id)
                               WHERE m.bank_ref = ?""", (bank_ref,)).fetchall()
        if not lines:
            raise ValueError("There is no proposed match to accept")
        residuals = {r[0]: r[1:] for r in con.execute(
            """SELECT original_invoice_id, amount, residual_type, reason, due_date
               FROM proposed_residuals WHERE bank_ref = ?""", (bank_ref,))}
        today = datetime.now().strftime("%Y-%m-%d")

        for invoice_id, applied, open_amount, customer_id in lines:
            residual = residuals.get(invoice_id)
            residual_amount = residual[0] if residual else 0.0
            remaining = round(open_amount - applied - residual_amount, 2)
            if remaining < -TOLERANCE:
                raise ValueError(f"{invoice_id}: payment is more than the open amount (handle as unapplied cash)")

            # 1. the original invoice: cleared, or partially paid if something is still open on it
            status = "cleared" if remaining <= TOLERANCE else "partially_paid"
            con.execute("""UPDATE invoices SET open_amount = ?, status = ?, cleared_date = ?, clearing_doc = ?
                           WHERE invoice_id = ?""",
                        (max(remaining, 0.0), status, payment_date if status == "cleared" else None,
                         bank_ref, invoice_id))

            # 2. the residual item: a new open item for the short pay, keeping the original due date
            if residual:
                con.execute("""INSERT INTO invoices (invoice_id, customer_id, invoice_date, due_date,
                                                     amount, open_amount, status, cleared_date, clearing_doc)
                               VALUES (?, ?, ?, ?, ?, ?, 'open', NULL, NULL)""",
                            (f"{invoice_id}-R1", customer_id, payment_date, residual[3],
                             residual_amount, residual_amount))

        # 3. learn the payer's bank account, and 4. record who approved it and when
        customers = {l[3] for l in lines}
        learned = _learn_account(con, bank_ref, lines[0][3], today) if len(customers) == 1 \
            else "Invoices of several customers; account not learned"
        con.execute("INSERT INTO decisions VALUES (?, 'Posted', ?, ?, ?, ?)",
                    (bank_ref, ",".join(sorted(customers)), user,
                     datetime.now().strftime("%Y-%m-%d %H:%M"), learned))
        con.commit()                              # all four steps are saved together...
        return f"Posted {len(lines)} invoice(s), {len(residuals)} residual item(s). {learned}"
    except Exception:
        con.rollback()                            # ...or none of them are
        raise
    finally:
        con.close()


# ---------------------------------------------------------------
# Reject: the proposal is wrong, so the payment goes to exceptions
# ---------------------------------------------------------------
def reject(bank_ref, user, note):
    if not note.strip():
        raise ValueError("Please give a reason for rejecting")
    con = _connect()
    try:
        if _already_decided(con, bank_ref):
            raise ValueError(f"{bank_ref} was already {_already_decided(con, bank_ref).lower()}")
        con.execute("INSERT INTO decisions VALUES (?, 'Rejected', NULL, ?, ?, ?)",
                    (bank_ref, user, datetime.now().strftime("%Y-%m-%d %H:%M"), note.strip()))
        con.commit()
        return "Sent to exceptions"
    finally:
        con.close()


# ---------------------------------------------------------------
# Post every Auto proposal in one go
# ---------------------------------------------------------------
def post_all_auto(user):
    con = sqlite3.connect(DB_PATH)
    refs = [r[0] for r in con.execute("SELECT DISTINCT bank_ref FROM match_results WHERE status = 'Auto'")]
    con.close()
    posted, skipped = 0, []
    for ref in refs:
        try:
            accept(ref, f"{user} (Auto rule)")
            posted += 1
        except ValueError as e:
            if "already" not in str(e):
                skipped.append(f"{ref}: {e}")
    return posted, skipped


# ---------------------------------------------------------------
# Reset: undo all postings (for demos and testing)
# ---------------------------------------------------------------
def reset_demo():
    if os.path.exists(SNAPSHOT):
        shutil.copy(SNAPSHOT, DB_PATH)
        os.remove(SNAPSHOT)
