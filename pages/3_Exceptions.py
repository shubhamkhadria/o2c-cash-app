"""
pages/3_Exceptions.py  -  Week 9: the exceptions workspace.

The agent investigates payments the rules could not settle and proposes one action with its
evidence. The person decides: approve (posting.py posts it, with the usual checks and log) or decline.
"""

import streamlit as st

from o2c import agent, posting
from o2c.db import query

st.set_page_config(page_title="Exceptions", layout="wide")
posting.setup()
agent.setup()
user = st.sidebar.text_input("Signed in as", value="AR analyst")
st.title("Exceptions workspace")
st.caption("The agent investigates what the rules could not settle. It proposes; you decide.")
if "flash" in st.session_state:
    kind, message = st.session_state.pop("flash")
    getattr(st, kind)(message)


def act(action, *args):
    try:
        st.session_state["flash"] = ("success", action(*args))
    except ValueError as e:
        st.session_state["flash"] = ("error", str(e))
    st.rerun()


# ---------------------------------------------------------------
# Step 1: the queue - payments the rules could not settle and nobody has decided yet
# ---------------------------------------------------------------
queue = query("""
    SELECT m.status, b.bank_ref, b.payment_date, b.payer_name, b.amount
    FROM bank_payments b
    JOIN (SELECT DISTINCT bank_ref, status FROM match_results) m USING (bank_ref)
    LEFT JOIN decisions d USING (bank_ref)
    WHERE m.status IN ('Review', 'Exception') AND d.bank_ref IS NULL
    ORDER BY CASE m.status WHEN 'Exception' THEN 0 ELSE 1 END, b.amount DESC
""")
if queue.empty:
    st.success("No open exceptions.")
    st.stop()

queue["agent_view"] = queue["bank_ref"].map(
    lambda r: (lambda i: "Not investigated yet" if i is None or i["outcome"] else f"{i['confidence']}: {i['proposal']}")(agent.latest(r)))
st.dataframe(queue.style.format({"amount": "${:,.2f}"}), hide_index=True)

labels = {r.bank_ref: f"{r.status} | {r.payer_name} | ${r.amount:,.2f}" for r in queue.itertuples()}
ref = st.selectbox("Open an exception", list(labels), format_func=labels.get)

# ---------------------------------------------------------------
# Step 2: run the agent (or show its latest open investigation)
# ---------------------------------------------------------------
inv = agent.latest(ref)
if inv is None or inv["outcome"]:
    planner = st.radio("Reasoning", ["playbook", "llm"], horizontal=True,
                       help="playbook: fixed steps in code. llm: a local model (Ollama) chooses the steps itself")
    if st.button("Run the investigation", type="primary"):
        try:
            with st.spinner("Investigating... (the local model can take a minute)"):
                agent.run(ref, planner)
        except Exception as e:                       # e.g. Ollama not running
            st.session_state["flash"] = ("error", f"The investigation could not run: {e}")
        st.rerun()
    st.stop()

left, right = st.columns([3, 2])
with left:
    st.subheader("Investigation log")
    st.caption(f"Run {inv['run_at']} · reasoning: {inv['planner']} · every step uses a read-only tool")
    if inv["planner"] == "llm":
        st.caption("The model chose each tool call below; the rules worked out the amounts and checked the guardrails.")
    for n, step in enumerate(inv["steps"], start=1):
        st.markdown(f"**{n}. {step['tool']}**  \n{step['finding']}")

with right:
    st.subheader("The agent's view")
    st.info(inv["hypothesis"].replace("$", r"\$")) 
    st.metric("Confidence", inv["confidence"])
    st.write(f"**Proposal:** {inv['proposal']}".replace("$", r"\$"))
    if inv["lines"]:
        st.dataframe([{"invoice": i, "apply": f"${a:,.2f}"} for i, a in inv["lines"]], hide_index=True)
    if inv["residuals"]:
        st.dataframe([{"new item": f"{r[0]}-R1", "amount": f"${r[2]:,.2f}", "type": r[3], "reason": r[4]}
                      for r in inv["residuals"]], hide_index=True)
    st.write("**Guardrails**")
    for name, ok in inv["guardrails"]:
        st.write(f"{'✓' if ok else '✗'} {name}")

# ---------------------------------------------------------------
# Step 3: the person decides
# ---------------------------------------------------------------
st.divider()
a, b = st.columns(2)
with a:
    if st.button("Approve the proposal", type="primary"):
        act(agent.approve, ref, user)
with b:
    note = st.text_input("Why is the proposal wrong?")
    if st.button("Decline"):
        act(agent.decline, ref, user, note)
