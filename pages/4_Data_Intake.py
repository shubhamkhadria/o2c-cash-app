"""
pages/4_Data_Intake.py  -  Week 10: load a customer's own files, in whatever layout they come.

Upload a file, the mapping agent proposes which column feeds which field, you confirm,
the rules check the data, and only then does it load (with a backup first).
Remittance emails are saved to the inbox and read by the AI extractor.
The sidebar runs the pipeline (extract, identify payer, match) on whatever is loaded.
"""

import os
import subprocess
import sys
import time

import streamlit as st

from o2c import intake

INBOX = "data/remittances"
PIPELINE = [("Read remittances", "pipeline/extract_remittances.py"),
            ("Identify payers", "pipeline/identify_payer.py"),
            ("Match payments", "pipeline/matcher.py"),
            ("Grade against the answer key (test data only)", "tools/grade_matcher.py")]


def run_scripts(steps):
    """Run pipeline scripts one after another, exactly as you would in the terminal; stop at the first error.
    Each script's output is shown live, line by line, so a slow step (an AI model reading emails) never
    looks like a frozen screen."""
    out = []
    with st.status("Running the pipeline...", expanded=True) as status:
        for n, (label, script) in enumerate(steps, start=1):
            st.write(f"**Step {n} of {len(steps)}: {label}**")
            live = st.empty()
            started, lines = time.time(), []
            proc = subprocess.Popen([sys.executable, "-u", script], stdout=subprocess.PIPE,   # -u: print lines at once
                                    stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                lines.append(line)
                live.code("".join(lines[-12:]))                  # the last few lines, updating as they arrive
            proc.wait()
            seconds = time.time() - started
            out.append(f"=== {label}  ({script}, {seconds:.0f}s)\n" + "".join(lines)[-3000:])
            if proc.returncode != 0:
                out.append(f"Stopped: {script} failed.")
                status.update(label=f"Stopped at step {n}: {label}", state="error")
                return "\n".join(out)
            live.empty()
            st.write(f"✓ {label} ({seconds:.0f}s)")
        status.update(label="Pipeline finished", state="complete")
    return "\n".join(out)

st.set_page_config(page_title="Data intake", layout="wide")
user = st.sidebar.text_input("Signed in as", value="AR analyst")
st.title("Data intake")
st.caption("Bring your own files. The agent proposes the mapping; you confirm it; the rules decide if it loads.")
if "flash" in st.session_state:
    kind_, message = st.session_state.pop("flash")
    getattr(st, kind_)(message)

# ---------------------------------------------------------------
# Sidebar: run the pipeline on whatever is loaded now
# ---------------------------------------------------------------
st.sidebar.write("**After loading:**")
if st.sidebar.button("Run the pipeline"):
    st.session_state["pipeline_log"] = run_scripts(PIPELINE)
    st.rerun()
if "pipeline_log" in st.session_state:
    with st.expander("Pipeline output", expanded=True):
        st.code(st.session_state["pipeline_log"])
        st.caption("Open Cash Application or Exceptions to work the results.")

# ---------------------------------------------------------------
# Step 1: what are you loading, and the file itself
# ---------------------------------------------------------------
kind = st.radio("What are you loading?", list(intake.TARGETS) + ["Remittance emails"], horizontal=True)
st.caption({"Customer master": "Adds new customers and updates known ones; never deletes.",
            "Open AR": "Load the customer master first: every open item must belong to a known customer. "
                       "Replaces today's open items; cleared history stays.",
            "Bank file": "Replaces the current bank day. Only customer receipts load; debits are set aside.",
            "Remittance emails": "Saved to the remittance inbox, then read by the AI extractor."}[kind])

# Remittance emails: no columns to map - save them, then let the extractor read them
if kind == "Remittance emails":
    emails = st.file_uploader("Emails (.txt)", type=["txt"], accept_multiple_files=True)
    if not emails:
        st.stop()
    clear = st.checkbox("Empty the inbox first (a new day)", value=True)
    if st.button(f"Save {len(emails)} email(s) and read them with AI", type="primary"):
        os.makedirs(INBOX, exist_ok=True)
        if clear:
            for old in os.listdir(INBOX):
                os.remove(os.path.join(INBOX, old))
        for e in emails:
            with open(os.path.join(INBOX, os.path.basename(e.name)), "wb") as f:
                f.write(e.getvalue())
        st.session_state["pipeline_log"] = run_scripts(PIPELINE[:1])
        st.rerun()
    st.stop()

upload = st.file_uploader("File (CSV or Excel)", type=["csv", "xlsx"])
if upload is None:
    st.stop()

df = intake.read_upload(upload.name, upload.getvalue())
skipped = df.attrs.get("skipped_lines", 0)
st.write(f"**{upload.name}**: {len(df)} rows, {len(df.columns)} columns"
         + (f" (skipped {skipped} report line(s) above the headers)" if skipped else ""))
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
for note in clean.attrs.get("notes", []):
    st.info(note)
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
        st.session_state["flash"] = ("success", message + (" Then click 'Run the pipeline' in the sidebar."
                                                           if kind in ("Open AR", "Bank file") else ""))
        st.session_state.pop(state_key, None)
    except Exception as e:
        st.session_state["flash"] = ("error", f"Nothing was loaded: {e}")
    st.rerun()