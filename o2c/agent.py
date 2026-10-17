"""
o2c/agent.py  -  Week 9: the exceptions agent.

It investigates payments the rules could not settle, using READ-ONLY tools, and proposes one
action with its evidence. It never posts: a person approves on the Exceptions page, and
posting.py does the posting with its usual checks and audit log.

PLANNER = "playbook": the reasoning is a fixed set of steps (free, repeatable, no API key).
With an API key, an LLM could choose which tool to call next and write the hypothesis;
the tools, the guardrails and the investigation log would stay exactly the same.
"""

import json
import sqlite3
from datetime import datetime

from o2c import posting
from o2c.db import DB_PATH, query

PLANNER = "playbook"
TOLERANCE = 0.01
NOT_A_CUSTOMER_WORDS = {"REFUND", "VENDOR", "CM", "REBATE"}


# ---------------------------------------------------------------
# Step 1: the tools. Each one only READS; the agent cannot change anything with them.
# ---------------------------------------------------------------
def tool_payment(ref):
    return query("SELECT * FROM bank_payments WHERE bank_ref = ?", (ref,)).iloc[0].to_dict()


def tool_payer(ref):
    rows = query("""SELECT p.customer_id, c.name FROM payer_identification p
                    LEFT JOIN customers c USING (customer_id)
                    WHERE p.bank_ref = ? AND p.customer_id IS NOT NULL""", (ref,))
    return dict(zip(rows["customer_id"], rows["name"]))


def tool_remittance(ref):
    return query("""SELECT invoice_id, amount_paid, deduction_amount, deduction_reason, issues
                    FROM remittance_extractions WHERE bank_ref = ?""", (ref,))


def tool_invoice(invoice_id):
    rows = query("""SELECT i.invoice_id, i.customer_id, c.name, i.open_amount, i.due_date, i.status
                    FROM invoices i JOIN customers c USING (customer_id) WHERE i.invoice_id = ?""",
                 (invoice_id,))
    return rows.iloc[0].to_dict() if len(rows) else None


def tool_open_items(customer_ids):
    if not customer_ids:
        return query("SELECT * FROM open_items WHERE 1 = 0")
    marks = ",".join("?" * len(customer_ids))
    return query(f"SELECT invoice_id, customer_id, open_amount, due_date FROM open_items "
                 f"WHERE customer_id IN ({marks})", tuple(customer_ids))


# ---------------------------------------------------------------
# Step 2: two small checks for the most common human and AI misreads
# ---------------------------------------------------------------
def one_digit_apart(a, b):
    """90001806 vs 90001809: same length, exactly one character different."""
    return len(a) == len(b) and sum(x != y for x, y in zip(a, b)) == 1


def digits_swapped(a, b):
    """1,210.37 vs 1,120.37: the same digits with two neighbours swapped."""
    a, b = f"{a:.2f}", f"{b:.2f}"
    diffs = [i for i in range(len(a)) if a[i] != b[i]] if len(a) == len(b) else []
    return len(diffs) == 2 and diffs[1] == diffs[0] + 1 and a[diffs[0]] == b[diffs[1]] and a[diffs[1]] == b[diffs[0]]


# ---------------------------------------------------------------
# Step 3: the investigation (the playbook)
# ---------------------------------------------------------------
def investigate(ref):
    steps, lines, residuals = [], [], []

    def log(tool, finding):
        steps.append({"tool": tool, "finding": finding})

    pay = tool_payment(ref)
    amount = round(pay["amount"], 2)
    log("Read the bank payment", f"${amount:,.2f} from {pay['payer_name']}; text: '{pay['remittance_text'] or 'none'}'")

    payers = tool_payer(ref)
    log("Identify the payer", ", ".join(f"{n} ({c})" for c, n in payers.items()) or "No customer account or name matches")

    # Case A: nobody we know, and the words say refund -> not an AR item at all
    if not payers:
        words = set(f"{pay['payer_name']} {pay['remittance_text']}".upper().split())
        if words & NOT_A_CUSTOMER_WORDS:
            log("Check the wording", f"Contains {', '.join(sorted(words & NOT_A_CUSTOMER_WORDS))}: typical of a supplier refund or credit memo")
            return _result(ref, steps, "Not a customer payment: looks like a supplier refund",
                           "route", [], [], "High", "Route to AP / treasury to record against the supplier")
        return _result(ref, steps, "Unknown payer and no remittance advice", "chase", [], [], "Low",
                       "Ask the bank for payer details, then chase the remittance")

    remit = tool_remittance(ref)
    log("Read the remittance advice", f"{len(remit)} line(s): " + "; ".join(
        f"{r.invoice_id} paid ${r.amount_paid:,.2f}" for r in remit.itertuples()) if len(remit) else "No remittance advice received")

    for r in remit.itertuples():
        inv = tool_invoice(r.invoice_id)
        if inv is None:
            log("Look up the invoice", f"{r.invoice_id} does not exist")
            continue
        log("Look up the invoice", f"{r.invoice_id} belongs to {inv['name']}, open ${inv['open_amount']:,.2f}")

        # Case B: the invoice belongs to someone else -> look for the payer's own near-identical invoice
        if inv["customer_id"] not in payers:
            own = tool_open_items(list(payers))
            near = own[own["invoice_id"].map(lambda x: one_digit_apart(x, r.invoice_id))
                       & ((own["open_amount"] - amount).abs() <= TOLERANCE)]
            log("Search the payer's open invoices", f"{len(own)} open; one digit from {r.invoice_id} with the exact amount: "
                + (", ".join(near["invoice_id"]) or "none"))
            if len(near) == 1:
                fix = near.iloc[0]
                lines.append((fix["invoice_id"], amount))
                return _result(ref, steps,
                               f"The remittance number {r.invoice_id} is another customer's invoice. The payer's own "
                               f"{fix['invoice_id']} is one digit different and matches ${amount:,.2f} exactly: a misread",
                               "match", lines, residuals, "High", f"Apply ${amount:,.2f} to {fix['invoice_id']}")
            continue

        # Case C: right invoice, but the numbers do not add up -> check for swapped digits
        open_amt = round(inv["open_amount"], 2)
        if abs(r.amount_paid + r.deduction_amount - open_amt) > TOLERANCE and digits_swapped(r.amount_paid, amount):
            ok = abs(amount + r.deduction_amount - open_amt) <= TOLERANCE
            log("Compare the amounts", f"Remittance says ${r.amount_paid:,.2f}, bank says ${amount:,.2f}: two digits swapped. "
                f"With the bank amount, paid + ${r.deduction_amount:,.2f} balance = ${amount + r.deduction_amount:,.2f} "
                f"{'equals' if ok else 'does not equal'} the open ${open_amt:,.2f}")
            if ok:
                lines.append((r.invoice_id, amount))
                rtype = "Balance due" if "partial" in (r.deduction_reason or "").lower() else "Deduction"
                residuals.append((r.invoice_id, inv["customer_id"], round(r.deduction_amount, 2), rtype,
                                  r.deduction_reason or "Short pay - to investigate", inv["due_date"]))
                return _result(ref, steps,
                               f"The remittance amount is a misread of the bank amount (${r.amount_paid:,.2f} vs ${amount:,.2f}). "
                               f"The customer paid part of {r.invoice_id} and the rest is a balance to follow",
                               "match", lines, residuals, "High",
                               f"Apply ${amount:,.2f} to {r.invoice_id} and open a ${r.deduction_amount:,.2f} {rtype.lower()} item")

    # Case D: fall back to the amount alone, within the payer's own open invoices
    own = tool_open_items(list(payers))
    exact = own[(own["open_amount"] - amount).abs() <= TOLERANCE]
    log("Search by amount", f"{len(exact)} of the payer's {len(own)} open invoices equal ${amount:,.2f}")
    if len(exact) == 1:
        lines.append((exact.iloc[0]["invoice_id"], amount))
        return _result(ref, steps, "One open invoice of this payer has exactly this amount", "match",
                       lines, residuals, "Medium", f"Apply ${amount:,.2f} to {exact.iloc[0]['invoice_id']}")
    return _result(ref, steps, "Not enough evidence to propose a match", "chase", [], [], "Low",
                   "Ask the payer for a remittance advice (remittance chase)")


# ---------------------------------------------------------------
# Step 4: guardrails, checked on every proposal before a person sees it
# ---------------------------------------------------------------
def _guardrails(ref, action, lines, residuals):
    checks = [("The agent does not post; a person must approve", True)]
    if action != "match":
        return checks
    pay = tool_payment(ref)
    payers = tool_payer(ref)
    invs = {i: tool_invoice(i) for i, _ in lines}
    checks.append(("Only the payer's own open invoices are used",
                   all(v is not None and v["customer_id"] in payers and v["status"] != "cleared" for v in invs.values())))
    checks.append(("Applied total equals the payment",
                   bool(abs(sum(a for _, a in lines) - pay["amount"]) <= TOLERANCE)))
    res = {r[0]: r[2] for r in residuals}
    checks.append(("No invoice is overpaid; any shortfall becomes a residual item",
                   all(abs(invs[i]["open_amount"] - a - res.get(i, 0)) <= TOLERANCE for i, a in lines)))
    return checks


def _result(ref, steps, hypothesis, action, lines, residuals, confidence, proposal):
    return {"bank_ref": ref, "planner": PLANNER, "steps": steps, "hypothesis": hypothesis, "action": action,
            "lines": lines, "residuals": residuals, "confidence": confidence, "proposal": proposal,
            "guardrails": _guardrails(ref, action, lines, residuals)}


# ---------------------------------------------------------------
# Step 5: save every investigation, and let a person approve or decline it
# ---------------------------------------------------------------
def setup():
    con = sqlite3.connect(DB_PATH)
    con.execute("""CREATE TABLE IF NOT EXISTS investigations (
        bank_ref TEXT, run_at TEXT, planner TEXT, hypothesis TEXT, action TEXT, proposal TEXT,
        confidence TEXT, details TEXT, outcome TEXT, decided_by TEXT, note TEXT)""")
    con.commit()
    con.close()


def run(ref):
    setup()
    result = investigate(ref)
    con = sqlite3.connect(DB_PATH)
    con.execute("INSERT INTO investigations VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                (ref, datetime.now().strftime("%Y-%m-%d %H:%M"), PLANNER, result["hypothesis"], result["action"],
                 result["proposal"], result["confidence"], json.dumps(result)))
    con.commit()
    con.close()
    return result


def latest(ref):
    rows = query("SELECT rowid, * FROM investigations WHERE bank_ref = ? ORDER BY rowid DESC LIMIT 1", (ref,))
    if rows.empty:
        return None
    row = rows.iloc[0].to_dict()
    row.update(json.loads(row.pop("details")))
    return row


def _close(rowid, outcome, user, note):
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE investigations SET outcome = ?, decided_by = ?, note = ? WHERE rowid = ?",
                (outcome, user, note, rowid))
    con.commit()
    con.close()


def approve(ref, user):
    inv = latest(ref)
    if inv is None or inv["outcome"]:
        raise ValueError("Run a new investigation first")
    if not all(ok for _, ok in inv["guardrails"]):
        raise ValueError("A guardrail failed, so this proposal cannot be approved")
    if inv["action"] == "match":
        con = sqlite3.connect(DB_PATH)                # replace the matcher's proposal with the agent's
        con.execute("DELETE FROM match_results WHERE bank_ref = ?", (ref,))
        con.execute("DELETE FROM proposed_residuals WHERE bank_ref = ?", (ref,))
        con.executemany("INSERT INTO match_results VALUES (?, ?, ?, 'Agent proposal', 'Review')",
                        [(ref, i, a) for i, a in inv["lines"]])
        con.executemany("INSERT INTO proposed_residuals VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [(ref, *r) for r in inv["residuals"]])
        con.commit()
        con.close()
        message = posting.accept(ref, f"{user} (approved agent proposal)")
    else:
        posting.reject(ref, user, f"{inv['proposal']} (approved agent proposal)")
        message = f"Removed from the AR queue: {inv['proposal']}"
    _close(inv["rowid"], "Approved", user, message)
    return message


def decline(ref, user, note):
    inv = latest(ref)
    if inv is None or inv["outcome"]:
        raise ValueError("Run a new investigation first")
    if not note.strip():
        raise ValueError("Please say why the proposal is wrong; it helps improve the playbook")
    _close(inv["rowid"], "Declined", user, note.strip())
    return "Proposal declined; the payment stays in the queue"
