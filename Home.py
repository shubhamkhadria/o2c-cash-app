"""
Home.py  -  Week 8: the front page of the cash application app (for the AR manager).

Run it with:   streamlit run Home.py
"""

import streamlit as st

from o2c import posting
from o2c.db import query, query_file

st.set_page_config(page_title="O2C Cash Application", layout="wide")
posting.setup()
user = st.sidebar.text_input("Signed in as", value="AR analyst")

st.title("Today's cash")
st.caption("Proposals from matcher v1.2. Nothing is posted to the ledger without approval.")
if "flash" in st.session_state:                     # message left by the last button click
    kind, message = st.session_state.pop("flash")
    getattr(st, kind)(message)

# ---------------------------------------------------------------
# Step 1: one row per bank payment: what the matcher proposed, and what happened since
# ---------------------------------------------------------------
payments = query("""
    SELECT b.bank_ref, b.amount,
           COALESCE(m.status, 'Exception')             AS proposal,
           COALESCE(d.decision, m.status, 'Exception') AS status
    FROM bank_payments b
    LEFT JOIN (SELECT DISTINCT bank_ref, status FROM match_results) m USING (bank_ref)
    LEFT JOIN decisions d USING (bank_ref)
""")
auto = payments[payments["proposal"] == "Auto"]
waiting_auto = payments[payments["status"] == "Auto"]          # Auto proposals not posted yet

# ---------------------------------------------------------------
# Step 2: the headline numbers
# ---------------------------------------------------------------
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Payments received", len(payments))
c2.metric("Cash received", f"${payments['amount'].sum():,.0f}")
c3.metric("Auto-applied", f"{len(auto) / len(payments):.0%}",
          help="Share of payments the matcher could apply without a person")
c4.metric("Needs a person", int(payments["status"].isin(["Review", "Exception"]).sum()))
c5.metric("Posted to ledger", int((payments["status"] == "Posted").sum()))

if len(waiting_auto):
    if st.button(f"Post {len(waiting_auto)} auto-applied payments to the ledger", type="primary"):
        posted, skipped = posting.post_all_auto(user)
        st.session_state["flash"] = ("success", f"Posted {posted} payments. Skipped: {len(skipped)}")
        st.rerun()                                  # run the page again so the numbers update

# ---------------------------------------------------------------
# Step 3: where the cash went, and how we knew who paid
# ---------------------------------------------------------------
left, right = st.columns(2)
with left:
    st.subheader("Where today's cash went")
    by_status = payments.groupby("status").agg(payments=("bank_ref", "count"), amount=("amount", "sum"))
    st.dataframe(by_status.style.format({"amount": "${:,.2f}"}))

with right:
    st.subheader("How we knew who paid")
    methods = query("""
        SELECT method, COUNT(DISTINCT bank_ref) AS payments
        FROM payer_identification GROUP BY method ORDER BY payments DESC
    """)
    st.dataframe(methods, hide_index=True)

# ---------------------------------------------------------------
# Step 4: new open items the matcher wants to create (residuals)
# ---------------------------------------------------------------
st.subheader("Deductions and balances to follow up")
residuals = query("""
    SELECT residual_type, COUNT(*) AS items, ROUND(SUM(amount), 2) AS amount
    FROM proposed_residuals GROUP BY residual_type
""")
st.dataframe(residuals.style.format({"amount": "${:,.2f}"}), hide_index=True)

# ---------------------------------------------------------------
# Step 5: AR aging, reusing your week 3 query unchanged (it moves when you post)
# ---------------------------------------------------------------
st.subheader("AR aging")
aging = query_file("sql/01_aging.sql")
st.bar_chart(aging, x="bucket", y="open_amount")
st.dataframe(aging.style.format({"open_amount": "${:,.2f}", "pct_of_ar": "{:.1f}%"}), hide_index=True)

# ---------------------------------------------------------------
# Demo helper: undo every posting
# ---------------------------------------------------------------
if st.sidebar.button("Reset demo data"):
    posting.reset_demo()
    st.session_state["flash"] = ("info", "All postings undone. The ledger is back to before the first posting.")
    st.rerun()
