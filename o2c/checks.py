"""
o2c/checks.py  -  Week 8 quick wins: daily health checks and the measures on the roadmap slide.

  health_checks()   the "daily close health report" for cash application: does everything still agree?
  measures()        unapplied cash, DSO, and accuracy against the answer key (test mode only)
  posting_file()    the file a customer's ERP would load to post what was approved
"""

import os
import sqlite3

from o2c.db import DB_PATH, query, query_file
from o2c.posting import SNAPSHOT

TOLERANCE = 0.01


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def _open_ar(db_path):
    """Total open AR in a database file (today's, or the copy saved before the first posting)."""
    con = sqlite3.connect(db_path)
    try:
        return con.execute("""SELECT COALESCE(SUM(open_amount), 0) FROM invoices
                              WHERE status IN ('open', 'partially_paid')""").fetchone()[0]
    finally:
        con.close()


def _posted_cash():
    return query("""SELECT COALESCE(SUM(b.amount), 0) AS cash
                    FROM decisions d JOIN bank_payments b USING (bank_ref)
                    WHERE d.decision = 'Posted'""")["cash"][0]


# ---------------------------------------------------------------
# Health checks: each one returns (name, passed, detail)
# ---------------------------------------------------------------
def health_checks():
    checks = []

    # 1. Every dollar posted must reduce open AR by exactly that dollar
    ar_before = _open_ar(SNAPSHOT) if os.path.exists(SNAPSHOT) else _open_ar(DB_PATH)
    ar_drop = ar_before - _open_ar(DB_PATH)
    cash = _posted_cash()
    gap = round(ar_drop - cash, 2) or 0.0             # "or 0.0" turns -0.0 into 0.0 for display
    checks.append(("Cash posted equals the drop in open AR", abs(gap) <= TOLERANCE,
                   f"Cash posted ${cash:,.2f}; AR dropped ${ar_drop:,.2f}; gap ${gap:,.2f}"))

    # 2. No invoice can be paid below zero
    negative = query("SELECT COUNT(*) AS n FROM invoices WHERE open_amount < -0.005")["n"][0]
    checks.append(("No invoice has a negative open amount", negative == 0,
                   f"{negative} invoice(s) below zero"))

    # 3. Every invoice cleared by a bank payment has a decision behind it (who approved it)
    orphans = query("""SELECT COUNT(*) AS n FROM invoices i
                       JOIN bank_payments b ON b.bank_ref = i.clearing_doc
                       LEFT JOIN decisions d ON d.bank_ref = i.clearing_doc
                       WHERE d.bank_ref IS NULL""")["n"][0]
    checks.append(("Every posting has an approval in the decision log", orphans == 0,
                   f"{orphans} invoice(s) cleared without a logged decision"))

    # 4. Every short pay on a posted payment became a residual item
    missing = query("""SELECT COUNT(*) AS n FROM proposed_residuals r
                       JOIN decisions d ON d.bank_ref = r.bank_ref AND d.decision = 'Posted'
                       LEFT JOIN invoices i ON i.invoice_id = r.original_invoice_id || '-R1'
                       WHERE i.invoice_id IS NULL""")["n"][0]
    checks.append(("Every short pay has its residual item", missing == 0,
                   f"{missing} residual item(s) missing"))
    return checks


# ---------------------------------------------------------------
# Measures from the roadmap: unapplied cash, DSO, accuracy
# ---------------------------------------------------------------
def measures():
    unapplied = query("""SELECT COALESCE(SUM(b.amount), 0) AS cash FROM bank_payments b
                         LEFT JOIN decisions d ON d.bank_ref = b.bank_ref AND d.decision = 'Posted'
                         WHERE d.bank_ref IS NULL""")["cash"][0]
    dso = query_file("sql/02_dso.sql")["dso_days"][0]

    # accuracy, test mode: only possible because the synthetic data has an answer key
    truth = query("SELECT bank_ref, invoice_id FROM bank_answer_key")
    true_sets = truth.groupby("bank_ref")["invoice_id"].apply(set).to_dict()
    posted = query("""SELECT d.bank_ref, m.invoice_id FROM decisions d
                      JOIN match_results m USING (bank_ref) WHERE d.decision = 'Posted'""")
    posted_sets = posted.groupby("bank_ref")["invoice_id"].apply(set).to_dict()
    correct = sum(1 for ref, invs in posted_sets.items() if invs == true_sets.get(ref, set()))
    accuracy = correct / len(posted_sets) if posted_sets else None

    return {"unapplied_cash": unapplied, "dso_days": dso,
            "accuracy": accuracy, "posted_checked": len(posted_sets)}


# ---------------------------------------------------------------
# The posting file: what the customer's ERP loads (files in, files out)
# ---------------------------------------------------------------
def posting_file():
    return query("""
        SELECT d.bank_ref                AS bank_reference,
               b.payment_date            AS posting_date,
               i.customer_id             AS customer,
               m.invoice_id              AS invoice_cleared,
               m.applied_amount          AS amount_applied,
               CASE WHEN r.amount IS NOT NULL THEN m.invoice_id || '-R1' END AS residual_item,
               r.amount                  AS residual_amount,
               r.residual_type           AS residual_type,
               r.reason                  AS residual_reason,
               d.decided_by              AS approved_by,
               d.decided_at              AS approved_at
        FROM decisions d
        JOIN bank_payments b USING (bank_ref)
        JOIN match_results m USING (bank_ref)
        JOIN invoices i ON i.invoice_id = m.invoice_id
        LEFT JOIN proposed_residuals r ON r.bank_ref = m.bank_ref AND r.original_invoice_id = m.invoice_id
        WHERE d.decision = 'Posted'
        ORDER BY d.decided_at, d.bank_ref
    """)
