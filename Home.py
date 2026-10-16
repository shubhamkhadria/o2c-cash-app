"""
Home.py  -  Week 8: the front page of the cash application app (for the AR manager).

Run it with:   streamlit run Home.py
"""

import streamlit as st

from o2c.db import query, query_file

st.set_page_config(page_title="O2C Cash Application", layout="wide")
st.title("Today’s cash, Gulf Coast AR")
st.caption("Proposals from matcher v1.2. Nothing is posted to the ledger without a person's approval.")

# ---------------------------------------------------------------
# Step 1: one row per bank payment, with the matcher's status
# ---------------------------------------------------------------
payments = query("""
    SELECT b.bank_ref, b.amount, COALESCE(m.status, 'Exception') AS status
    FROM bank_payments b
    LEFT JOIN (SELECT DISTINCT bank_ref, status FROM match_results) m USING (bank_ref)
""")
auto = payments[payments["status"] == "Auto"]

# ---------------------------------------------------------------
# Step 2: the headline numbers
# ---------------------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
c1.metric("Payments received", len(payments))
c2.metric("Cash received", f"${payments['amount'].sum():,.0f}")
c3.metric("Auto-applied", f"{len(auto) / len(payments):.0%}",
          help="Share of payments the matcher could apply without a person")
c4.metric("Needs a person", len(payments) - len(auto))

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
# Step 5: AR aging, reusing your week 3 query unchanged
# ---------------------------------------------------------------
st.subheader("AR aging")
aging = query_file("sql/01_aging.sql")
st.bar_chart(aging, x="bucket", y="open_amount")
st.dataframe(aging.style.format({"open_amount": "${:,.2f}", "pct_of_ar": "{:.1f}%"}), hide_index=True)
