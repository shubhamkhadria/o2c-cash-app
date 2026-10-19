"""
o2c/intake.py  -  Week 10: load a customer's own files (any layout) into our standard tables.

  propose_mapping(kind, df, planner)   which uploaded column feeds which of our fields
                                        "saved": a layout a person already confirmed before
                                        "playbook": header names compared with a list of synonyms (no AI)
                                        "llm": a model reads the headers AND sample values (Ollama)
  check(kind, df, mapping)              rules: required fields, dates and amounts parse, no duplicates,
                                        every invoice has a known customer. Returns the clean table + problems
  load(kind, clean, mapping, user, filename)   backup, write the table, remember the mapping, log it

The AI only proposes the mapping. A person confirms it, and the rules decide whether the data may load.
"""

import difflib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime

import pandas as pd

from o2c.db import DB_PATH

BACKUP_DIR = "backups"                     # *.db files are ignored by git
CLOUD_KEY = os.environ.get("OLLAMA_API_KEY")
MODEL = "gpt-oss:20b" if CLOUD_KEY else "qwen2.5:3b"

# ---------------------------------------------------------------
# Step 1: our standard fields for each kind of file
#   field: (required?, type, what it means, everyday names a header might use)
# ---------------------------------------------------------------
TARGETS = {
    "Customer master": {
        "customer_id":        (True,  "text",   "the customer's account number", ["customer", "customer id", "customer no", "customer number", "account", "account no", "cust"]),
        "name":               (True,  "text",   "the customer's legal name", ["name", "customer name", "company", "company name"]),
        "region":             (False, "text",   "sales region or state", ["region", "state", "territory", "area"]),
        "payment_terms_days": (False, "int",    "payment terms in days, e.g. Net 30 -> 30", ["terms", "payment terms", "terms days", "net days"]),
        "credit_limit":       (False, "amount", "credit limit", ["credit limit", "limit", "credit"]),
    },
    "Open AR": {
        "invoice_id":   (True,  "text",   "the invoice (document) number", ["invoice", "invoice id", "invoice no", "invoice number", "document", "document number", "doc no"]),
        "customer_id":  (True,  "text",   "the customer's account number", ["customer", "customer id", "customer no", "customer number", "account", "cust"]),
        "invoice_date": (False, "date",   "the invoice (document) date", ["invoice date", "document date", "doc date", "date"]),
        "due_date":     (True,  "date",   "the date payment is due", ["due date", "due", "net due date", "payment due"]),
        "amount":       (False, "amount", "the original invoice amount", ["amount", "invoice amount", "original amount", "gross amount"]),
        "open_amount":  (True,  "amount", "the amount still open (unpaid)", ["open amount", "open", "balance", "amount due", "outstanding", "remaining"]),
    },
}


# ---------------------------------------------------------------
# Step 2: propose a mapping  {our field: (uploaded column or None, reason)}
# ---------------------------------------------------------------
def _layout(kind, df):
    """A fingerprint of the file layout: the kind plus its column names."""
    return kind + "|" + "|".join(sorted(str(c).strip().lower() for c in df.columns))


def saved_mapping(kind, df):
    con = sqlite3.connect(DB_PATH)
    try:
        _setup(con)
        row = con.execute("SELECT mapping, confirmed_by FROM intake_mappings WHERE layout = ?",
                          (_layout(kind, df),)).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    mapping = json.loads(row[0])
    return {f: (c, f"Confirmed earlier by {row[1]} for this exact layout") for f, c in mapping.items()}


def _simple(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()


def _playbook(kind, df):
    """No AI: compare each header with our field names and their everyday synonyms."""
    proposal, used = {}, set()
    for field, (_, _, _, synonyms) in TARGETS[kind].items():
        best, score = None, 0.0
        for col in df.columns:
            if col in used:
                continue
            s = max(difflib.SequenceMatcher(None, _simple(col), _simple(w)).ratio()
                    for w in synonyms + [field.replace("_", " ")])
            if s > score:
                best, score = col, s
        if score >= 0.75:
            used.add(best)
            proposal[field] = (best, f"Header looks like '{field}' ({score:.0%} similar)")
        else:
            proposal[field] = (None, "No header looks similar enough")
    return proposal


LLM_PROMPT = """You map columns of an uploaded finance file to a standard data model.
Use the column names AND the sample values (codes like SAP field names are common, e.g. KUNNR = customer).
Return ONLY JSON: {"<field>": {"column": "<exact uploaded column name or null>", "reason": "<short reason>"}}
for every field listed. Use each uploaded column at most once. Use null if nothing fits; never guess. For open items: if the file has only one amount column, it is the open_amount."""

def _llm(kind, df, client=None):
    if client is None:
        import ollama                                     # only needed for this planner
        client = (ollama.Client(host="https://ollama.com", headers={"Authorization": f"Bearer {CLOUD_KEY}"})
                  if CLOUD_KEY else ollama)
    fields = "\n".join(f"- {f}: {d} ({'required' if req else 'optional'}, {t})"
                       for f, (req, t, d, _) in TARGETS[kind].items())
    sample = df.head(3).to_csv(index=False)
    reply = client.chat(model=MODEL, format="json", options={"temperature": 0}, messages=[
        {"role": "system", "content": LLM_PROMPT},
        {"role": "user", "content": f"File type: {kind}\nFields:\n{fields}\n\nUploaded file (first rows):\n{sample}"}])
    answer = json.loads(reply.message.content)

    proposal, used = {}, set()                            # the rules check the model's answer
    for field in TARGETS[kind]:
        item = answer.get(field) or {}
        col = item.get("column") if isinstance(item, dict) else item
        reason = (item.get("reason") if isinstance(item, dict) else None) or "(no reason given)"
        if col not in df.columns:
            col, reason = None, (f"Model suggested '{col}', which is not a column in the file" if col else reason)
        elif col in used:
            col, reason = None, "Model used this column twice; left for you to choose"
        if col:
            used.add(col)
        proposal[field] = (col, reason)
    return proposal


def propose_mapping(kind, df, planner="playbook", client=None):
    saved = saved_mapping(kind, df)
    if saved:
        return saved, "saved"
    return (_llm(kind, df, client) if planner == "llm" else _playbook(kind, df)), planner


# ---------------------------------------------------------------
# Step 3: the rules - convert the values and list every problem
# ---------------------------------------------------------------
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d.%m.%Y", "%Y%m%d", "%d/%m/%Y"]


def _to_date(values):
    """Use the known format that reads the most values; values it cannot read are flagged, never guessed."""
    text = values.astype(str).str.strip()
    tries = [pd.to_datetime(text, format=fmt, errors="coerce") for fmt in DATE_FORMATS]
    best = max(tries, key=lambda parsed: parsed.notna().sum())
    return best.dt.strftime("%Y-%m-%d")


def _to_amount(values):
    """'$1,234.50' -> 1234.5 ; '1.234,50' stays unreadable (flagged) ; '125.00-' (SAP minus) -> -125.0"""
    def one(v):
        v = str(v).strip().replace("$", "").replace(",", "").replace(" ", "")
        if v.endswith("-"):
            v = "-" + v[:-1]
        try:
            return float(v) if v else None
        except ValueError:
            return None
    return values.map(one)


def _to_int(values):
    """'NT30' or 'Net 30' -> 30"""
    return values.map(lambda v: int(m.group()) if (m := re.search(r"\d+", str(v))) else None)


def check(kind, df, mapping):
    """Returns (clean table, problems). Nothing loads while there are problems."""
    problems = []
    targets = TARGETS[kind]
    for field, (req, *_rest) in targets.items():
        if req and not mapping.get(field):
            problems.append(f"Required field '{field}' is not mapped")
    if problems:
        return None, problems

    clean = pd.DataFrame(index=df.index)
    for field, (req, ftype, *_rest) in targets.items():
        col = mapping.get(field)
        if not col:
            continue
        raw = df[col].astype(str).str.strip()
        convert = {"date": _to_date, "amount": _to_amount, "int": _to_int}.get(ftype)
        clean[field] = convert(raw) if convert else raw
        bad = clean[field].isna() & (raw != "")
        if bad.any():
            problems.append(f"{field}: {bad.sum()} value(s) could not be read, e.g. '{raw[bad].iloc[0]}' (row {bad.idxmax() + 2})")
        missing = (raw == "") if req else pd.Series(False, index=df.index)
        if missing.any():
            problems.append(f"{field}: {missing.sum()} row(s) are empty but the field is required")

    key = list(targets)[0]                                       # customer_id or invoice_id
    dupes = clean[key][clean[key].duplicated()]
    if len(dupes):
        problems.append(f"{key}: {len(dupes)} duplicate(s), e.g. {dupes.iloc[0]}")

    if kind == "Open AR":
        if "amount" not in clean:
            clean["amount"] = clean["open_amount"]               # an open-items extract often has one amount
        clean["amount"] = clean["amount"].fillna(clean["open_amount"])
        if (clean["open_amount"] <= 0).any():
            problems.append(f"open_amount: {(clean['open_amount'] <= 0).sum()} row(s) are zero or negative "
                            "(credits are not supported yet)")
        if (clean["open_amount"] > clean["amount"] + 0.01).any():
            problems.append("open_amount is larger than amount on some rows")
        known = set(_existing("SELECT customer_id FROM customers"))
        unknown = sorted(set(clean["customer_id"]) - known)
        if unknown:
            problems.append(f"{len(unknown)} customer(s) are not in the customer master, e.g. {unknown[0]}; "
                            "load the customer master first")
        cleared = set(_existing("SELECT invoice_id FROM invoices WHERE status = 'cleared'"))
        clash = sorted(set(clean["invoice_id"]) & cleared)
        if clash:
            problems.append(f"{len(clash)} invoice(s) are already cleared in the ledger, e.g. {clash[0]}")
    return clean, problems


def _existing(sql):
    con = sqlite3.connect(DB_PATH)
    try:
        return [r[0] for r in con.execute(sql)]
    finally:
        con.close()


def summary(kind, clean):
    if kind == "Customer master":
        return {"Rows": len(clean), "Customers": clean["customer_id"].nunique()}
    return {"Rows": len(clean), "Customers": clean["customer_id"].nunique(),
            "Total open": f"${clean['open_amount'].sum():,.2f}"}


# ---------------------------------------------------------------
# Step 4: load - backup first, one transaction, remember the mapping, log it
# ---------------------------------------------------------------
def _setup(con):
    con.execute("""CREATE TABLE IF NOT EXISTS intake_mappings (
        layout TEXT PRIMARY KEY, kind TEXT, mapping TEXT, confirmed_by TEXT, confirmed_at TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS intake_log (
        loaded_at TEXT, kind TEXT, filename TEXT, rows INTEGER, total REAL, planner TEXT, loaded_by TEXT)""")


def load(kind, df, clean, mapping, user, filename, planner):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy(DB_PATH, os.path.join(BACKUP_DIR, f"o2c_before_intake_{stamp}.db"))

    con = sqlite3.connect(DB_PATH)
    try:
        _setup(con)
        if kind == "Customer master":                            # add new customers, update known ones
            for r in clean.itertuples(index=False):
                row = r._asdict()
                con.execute("INSERT OR IGNORE INTO customers (customer_id, name) VALUES (?, ?)",
                            (row["customer_id"], row["name"]))
                sets = {k: v for k, v in row.items() if k != "customer_id" and pd.notna(v)}
                con.execute(f"UPDATE customers SET {', '.join(f'{k} = ?' for k in sets)} WHERE customer_id = ?",
                            (*sets.values(), row["customer_id"]))
            total = None
        else:                                                    # replace today's open items; keep cleared history
            con.execute("DELETE FROM invoices WHERE status IN ('open', 'partially_paid')")
            for r in clean.itertuples(index=False):
                row = r._asdict()
                status = "partially_paid" if row["open_amount"] < row["amount"] - 0.01 else "open"
                con.execute("""INSERT INTO invoices (invoice_id, customer_id, invoice_date, due_date,
                                   amount, open_amount, status) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (row["invoice_id"], row["customer_id"], row.get("invoice_date"), row["due_date"],
                             row["amount"], row["open_amount"], status))
            total = float(clean["open_amount"].sum())
        now = datetime.now().isoformat(timespec="seconds")
        con.execute("INSERT OR REPLACE INTO intake_mappings VALUES (?, ?, ?, ?, ?)",
                    (_layout(kind, df), kind, json.dumps(mapping), user, now))
        con.execute("INSERT INTO intake_log VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (now, kind, filename, len(clean), total, planner, user))
        con.commit()
    except Exception:
        con.rollback()                                           # all or nothing
        raise
    finally:
        con.close()
    return f"Loaded {len(clean)} rows of {kind} from {filename}. Backup saved first; mapping remembered."
