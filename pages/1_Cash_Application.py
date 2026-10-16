"""
pages/1_Cash_Application.py  -  Week 8: the workbench where an AR analyst reviews proposals.

Streamlit turns every file in pages/ into a page in the sidebar automatically.
Session 1: look at the evidence. Session 2: Accept posts to the ledger, Reject sends to exceptions.
"""

import streamlit as st

from o2c import posting
from o2c.db import query

st.set_page_config(page_title="Cash Application", layout="wide")
posting.setup()
user = st.sidebar.text_input("Signed in as", value="AR analyst")
st.title("Cash application workbench")
if "flash" in st.session_state:                     # message left by the last button click
    kind, message = st.session_state.pop("flash")
    getattr(st, kind)(message)


def act(action, *args):
    """Run Accept or Reject, keep the result message, and redraw the page."""
    try:
        st.session_state["flash"] = ("success", action(*args))
    except ValueError as e:                          # our own checks, e.g. "already posted"
        st.session_state["flash"] = ("error", str(e))
    st.rerun()


# ---------------------------------------------------------------
# Step 1: the queue - every payment with its status, the riskiest first
# ---------------------------------------------------------------
payments = query("""
    SELECT COALESCE(d.decision, m.status, 'Exception') AS status, b.bank_ref, b.payment_date,
           b.payer_name, b.amount, b.remittance_text, a.account_last4
    FROM bank_payments b
    LEFT JOIN payment_payer_accounts a USING (bank_ref)
    LEFT JOIN (SELECT DISTINCT bank_ref, status FROM match_results) m USING (bank_ref)
    LEFT JOIN decisions d USING (bank_ref)
    ORDER BY CASE COALESCE(d.decision, m.status, 'Exception') WHEN 'Exception' THEN 0 WHEN 'Review' THEN 1 ELSE 2 END,
             b.amount DESC
""")

view = st.radio("Show", ["Needs a person", "All payments"], horizontal=True)
if view == "Needs a person":
    payments = payments[payments["status"].isin(["Review", "Exception"])]

st.dataframe(payments[["status", "bank_ref", "payment_date", "payer_name", "amount"]]
             .style.format({"amount": "${:,.2f}"}), hide_index=True)

if payments.empty:
    st.success("Nothing waiting for review.")
    st.stop()                                              # end the page here

# ---------------------------------------------------------------
# Step 2: pick one payment to look at
# ---------------------------------------------------------------
labels = {r.bank_ref: f"{r.status} | {r.payer_name} | ${r.amount:,.2f}" for r in payments.itertuples()}
ref = st.selectbox("Open a payment", list(labels), format_func=labels.get)
pay = payments.set_index("bank_ref").loc[ref]

who = query("""
    SELECT p.method, p.customer_id, c.name, p.score
    FROM payer_identification p LEFT JOIN customers c USING (customer_id)
    WHERE p.bank_ref = ?
""", (ref,))
advice = query("""
    SELECT invoice_id, amount_paid, deduction_amount, deduction_reason, validation_status
    FROM remittance_extractions WHERE bank_ref = ?
""", (ref,))
lines = query("""
    SELECT m.invoice_id, i.customer_id, i.due_date, i.open_amount, m.applied_amount, m.rule
    FROM match_results m LEFT JOIN invoices i USING (invoice_id)
    WHERE m.bank_ref = ? AND m.invoice_id IS NOT NULL
""", (ref,))
residuals = query("""
    SELECT original_invoice_id, residual_type, amount, reason, due_date
    FROM proposed_residuals WHERE bank_ref = ?
""", (ref,))

# ---------------------------------------------------------------
# Step 3: the evidence side by side - what came in, who sent it, what we propose
# ---------------------------------------------------------------
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("1. Bank payment")
    st.metric("Amount", f"${pay['amount']:,.2f}")
    st.write(f"**Date:** {pay['payment_date']}")
    st.write(f"**Payer name (from bank):** {pay['payer_name']}")
    st.write(f"**From account:** ****{pay['account_last4']}")
    st.write("**Remittance text:**")
    st.code(pay["remittance_text"] or "(none)", language=None)

with col2:
    st.subheader("2. Who paid")
    candidates = set(who["customer_id"].dropna())
    if not candidates:
        st.error("Unidentified: no account or name match")
    elif len(candidates) > 1:
        st.warning(f"Group account: {len(candidates)} possible customers")
    else:
        st.success(f"Identified by {who['method'].iloc[0]}")
    st.dataframe(who, hide_index=True)
    if len(advice):
        st.write("**Remittance advice (read by AI, then validated):**")
        st.dataframe(advice, hide_index=True)

with col3:
    st.subheader("3. Proposed match")
    if lines.empty:
        st.error("No match found. This goes to exceptions.")
    else:
        st.write(f"**Status:** {pay['status']}  |  **Rule:** {lines['rule'].iloc[0]}")
        st.dataframe(lines.style.format({"open_amount": "${:,.2f}", "applied_amount": "${:,.2f}"}),
                     hide_index=True)
        invoice_customers = set(lines["customer_id"].dropna())
        if candidates and invoice_customers <= candidates:
            st.success("The invoices belong to the identified payer")
        elif candidates:
            st.warning("The invoices belong to a different customer than the payer. Check before accepting.")
    if len(residuals):
        st.write("**New open items it would create:**")
        st.dataframe(residuals.style.format({"amount": "${:,.2f}"}), hide_index=True)

# ---------------------------------------------------------------
# Step 4: the decision - a person approves or rejects, and it is logged
# ---------------------------------------------------------------
st.divider()
decision = query("SELECT * FROM decisions WHERE bank_ref = ?", (ref,))
if len(decision):
    d = decision.iloc[0]
    st.info(f"{d['decision']} by {d['decided_by']} on {d['decided_at']}. {d['note']}")
else:
    a, r = st.columns(2)
    with a:
        if st.button("Accept and post", type="primary", disabled=lines.empty):
            act(posting.accept, ref, user)
    with r:
        note = st.text_input("Reason for rejecting")
        if st.button("Reject"):
            act(posting.reject, ref, user, note)
