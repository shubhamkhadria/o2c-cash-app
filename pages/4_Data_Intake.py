"""
pages/4_Data_Intake.py  -  Week 10: load a customer's own files, in whatever layout they come.

Upload a file, the mapping agent proposes which column feeds which field, you confirm,
the rules check the data, and only then does it load (with a backup first).
"""

import pandas as pd
import streamlit as st

from o2c import intake

st.set_page_config(page_title="Data intake", layout="wide")
user = st.sidebar.text_input("Signed in as", value="AR analyst")
st.title("Data intake")
st.caption("Bring your own files. The agent proposes the mapping; you confirm it; the rules decide if it loads.")
if "flash" in st.session_state:
    kind_, message = st.session_state.pop("flash")
    getattr(st, kind_)(message)

# ---------------------------------------------------------------
# Step 1: what are you loading, and the file itself
# ---------------------------------------------------------------
kind = st.radio("What are you loading?", list(intake.TARGETS), horizontal=True)
if kind == "Open AR":
    st.info("Load the customer master first: every open item must belong to a known customer. "
            "Loading open AR replaces today's open items; cleared history stays.")
upload = st.file_uploader("File (CSV or Excel)", type=["csv", "xlsx"])
if upload is None:
    st.stop()

df = (pd.read_excel(upload, dtype=str) if upload.name.endswith(".xlsx")
      else pd.read_csv(upload, dtype=str, keep_default_na=False)).fillna("")
df.columns = [str(c).strip() for c in df.columns]
st.write(f"**{upload.name}**: {len(df)} rows, {len(df.columns)} columns")
st.dataframe(df.head(5), hide_index=True)

# ---------------------------------------------------------------
# Step 2: the mapping agent proposes; a saved mapping is reused without asking again
# ---------------------------------------------------------------
state_key = f"proposal|{kind}|{upload.name}"
saved = intake.saved_mapping(kind, df)
if saved and state_key not in st.session_state:
    st.session_state[state_key] = (saved, "saved")

if state_key not in st.session_state:
    planner = st.radio("Who proposes the mapping?", ["playbook", "llm"], horizontal=True,
                       help="playbook: compares header names with a list of synonyms (no AI). "
                            "llm: a model reads the headers and sample values.")
    if st.button("Propose a mapping", type="primary"):
        try:
            with st.spinner("Reading the file..."):
                st.session_state[state_key] = intake.propose_mapping(kind, df, planner)
        except Exception as e:                                   # e.g. no model available
            st.session_state["flash"] = ("error", f"Could not propose a mapping: {e}")
        st.rerun()
    st.stop()

proposal, planner = st.session_state[state_key]
st.subheader("Mapping")
st.caption({"saved": "This layout was confirmed before, so its mapping is reused.",
            "llm": "Proposed by the model from the headers and sample values. Check every line.",
            "playbook": "Proposed by header-name similarity. Check every line."}[planner])

choices = ["(not in this file)"] + list(df.columns)
mapping = {}
for field, (required, ftype, meaning, _) in intake.TARGETS[kind].items():
    col, reason = proposal.get(field, (None, ""))
    left, right = st.columns([2, 3])
    with left:
        picked = st.selectbox(f"{field}{' *' if required else ''}", choices,
                              index=choices.index(col) if col in choices else 0,
                              help=f"{meaning} ({ftype})", key=f"{state_key}|{field}")
    with right:
        st.caption(f"Agent: {reason}")
        if picked != choices[0]:
            st.caption("Sample: " + ", ".join(df[picked].head(3).astype(str)))
    mapping[field] = None if picked == choices[0] else picked

# ---------------------------------------------------------------
# Step 3: the rules check the data with the mapping you confirmed
# ---------------------------------------------------------------
clean, problems = intake.check(kind, df, mapping)
st.subheader("Checks")
if problems:
    for p in problems:
        st.error(p)
    st.caption("Fix the mapping above or the file itself. Nothing loads while there are problems.")
    st.stop()

st.success("All checks passed")
cols = st.columns(len(intake.summary(kind, clean)))
for c, (label, value) in zip(cols, intake.summary(kind, clean).items()):
    c.metric(label, value)
st.dataframe(clean.head(10), hide_index=True)

# ---------------------------------------------------------------
# Step 4: load (a person clicks; a backup is taken first)
# ---------------------------------------------------------------
edited = mapping != {f: c for f, (c, _) in proposal.items()}       # did the person change the agent's proposal?
if edited:
    st.caption("You changed the agent's proposal; the log will say so.")
if st.button(f"Load {len(clean)} rows into {kind}", type="primary"):
    try:
        message = intake.load(kind, df, clean, mapping, user, upload.name,
                              planner + (" + edited by person" if edited else ""))
        st.session_state["flash"] = ("success", message + (" Re-run the pipeline (matcher) so it sees the new open items."
                                                           if kind == "Open AR" else ""))
        st.session_state.pop(state_key, None)
    except Exception as e:
        st.session_state["flash"] = ("error", f"Nothing was loaded: {e}")
    st.rerun()
