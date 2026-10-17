"""
pages/2_Decision_Log.py  -  Week 8 quick wins: the audit trail, and the posting file for the ERP.

Two claims from the deck, made visible:
  "Auditable by design"  -> every decision, who made it, when, and why
  "Files in, files out"  -> the posting file a customer's ERP would load
"""

from datetime import date

import streamlit as st

from o2c import posting
from o2c.checks import posting_file
from o2c.db import query

st.set_page_config(page_title="Decision Log", layout="wide")
posting.setup()
st.title("Decision log and posting file")

# ---------------------------------------------------------------
# Step 1: the decision log (who decided what, when, and why)
# ---------------------------------------------------------------
log = query("""
    SELECT d.decided_at, d.decision, d.decided_by, d.bank_ref, b.payer_name, b.amount, d.customer_id, d.note
    FROM decisions d JOIN bank_payments b USING (bank_ref)
    ORDER BY d.decided_at DESC, d.bank_ref
""")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Decisions logged", len(log))
c2.metric("Posted by a person", int(((log["decision"] == "Posted") & ~log["decided_by"].str.contains("Auto rule")).sum()))
c3.metric("Posted by an agreed Auto rule", int(log["decided_by"].str.contains("Auto rule").sum()))
c4.metric("Rejected to exceptions", int((log["decision"] == "Rejected").sum()))

show = st.radio("Show", ["All", "Posted", "Rejected"], horizontal=True)
if show != "All":
    log = log[log["decision"] == show]

if log.empty:
    st.info("No decisions yet. Post or reject a payment in the workbench, or post the Auto proposals on Home.")
else:
    st.dataframe(log.style.format({"amount": "${:,.2f}"}), hide_index=True)

# ---------------------------------------------------------------
# Step 2: the posting file (what the ERP loads; the product never writes into the ERP)
# ---------------------------------------------------------------
st.subheader("Posting file for the ERP")
st.caption("One row per invoice cleared. Short pays carry the new residual item, so the ERP can open it.")
file = posting_file()
if file.empty:
    st.info("Nothing posted yet, so there is nothing to send to the ERP.")
else:
    st.dataframe(file.style.format({"amount_applied": "${:,.2f}", "residual_amount": "${:,.2f}"},
                                   na_rep=""), hide_index=True)
    st.download_button("Download posting file (CSV)", file.to_csv(index=False),
                       file_name=f"posting_file_{date.today().isoformat()}.csv", mime="text/csv")
