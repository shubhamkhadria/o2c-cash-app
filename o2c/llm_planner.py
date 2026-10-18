"""
o2c/llm_planner.py  -  Week 9: a real agent loop with a local model (Ollama), no API key.

The model decides which read-only tool to call next, reads the results, and finishes by calling
`propose`. Everything around it stays the same as the playbook: the same tools, the same guardrails,
the same investigation log, and a person still approves. Rules still do the accounting: the model
says WHICH invoices; the code works out any residual amount from the ledger.

Needs:  ollama running in the codespace (ollama serve), a model pulled (ollama pull qwen2.5:3b),
        and the Python client (pip install ollama).
"""

import json
import os

from o2c import agent

CLOUD_KEY = os.environ.get("OLLAMA_API_KEY")          # a Codespaces secret; never written in code
MODEL = "gpt-oss:20b" if CLOUD_KEY else "qwen2.5:3b"
MAX_STEPS = 8
TOLERANCE = 0.01

SYSTEM_PROMPT = """You are an accounts receivable investigator. A bank payment could not be matched by the rules.
Find out what it pays, using ONLY the tools. Never guess an invoice number: every invoice you propose
must come from a tool result and must belong to the identified payer.
Typical steps: read the payment, identify the payer, read the remittance advice, look up each invoice it
names (check who owns it and its open amount), and list the payer's open invoices. Always read the remittance
advice before proposing. When a remittance invoice number or amount differs from the bank or the ledger,
call check_misread with the two values: it tells you if one is a misread of the other.
Say in the hypothesis if the remittance differs from the bank, and what check_misread found.
Finish by calling `propose` exactly once:
- action "match": the invoice_ids (comma-separated) the payment should be applied to
- action "route": not a customer payment (for example a supplier refund)
- action "chase": not enough evidence; ask the payer for a remittance advice
Keep the hypothesis to one or two sentences, citing the evidence."""


# ---------------------------------------------------------------
# Step 1: describe the tools to the model (name, purpose, inputs)
# ---------------------------------------------------------------
def _tool(name, description, **params):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "required": list(params),
                       "properties": {p: {"type": "string", "description": d} for p, d in params.items()}}}}


TOOLS = [
    _tool("get_payment", "The bank payment: amount, date, payer name as the bank sent it, remittance text",
          bank_ref="The bank reference of the payment"),
    _tool("get_payer", "Customers identified as the payer (by bank account, else by name)",
          bank_ref="The bank reference of the payment"),
    _tool("get_remittance", "Lines the customer's remittance advice claims to pay (read by AI, may contain misreads)",
          bank_ref="The bank reference of the payment"),
    _tool("get_invoice", "One invoice: which customer owns it, open amount, due date, status",
          invoice_id="The invoice number"),
    _tool("get_open_items", "All open invoices of one customer",
          customer_id="The customer id, for example 100034"),
    _tool("check_misread", "Compare a value from the remittance with one from the bank or ledger, to spot a misread",
          value_a="A value from the remittance advice (an invoice number or an amount)",
          value_b="The value to compare it with (from the bank payment or the ledger)"),
    _tool("propose", "Finish the investigation with one proposal",
          action="match, route or chase", invoice_ids="Comma-separated invoice ids for match, else empty",
          hypothesis="One or two sentences explaining the evidence", confidence="High, Medium or Low"),
]


# ---------------------------------------------------------------
# Step 2: run a tool the model asked for (all read-only, results kept short)
# ---------------------------------------------------------------
def _run_tool(name, args):
    if name == "get_payment":
        p = agent.tool_payment(args["bank_ref"])
        return {k: p[k] for k in ("bank_ref", "payment_date", "amount", "payer_name", "remittance_text")}
    if name == "get_payer":
        return agent.tool_payer(args["bank_ref"]) or "No customer identified"
    if name == "get_remittance":
        r = agent.tool_remittance(args["bank_ref"])
        return r[["invoice_id", "amount_paid", "deduction_amount", "deduction_reason"]].to_dict("records") or "No remittance advice"
    if name == "get_invoice":
        return agent.tool_invoice(args["invoice_id"]) or "No such invoice"
    if name == "get_open_items":
        return agent.tool_open_items([args["customer_id"]]).to_dict("records") or "No open items"
    if name == "check_misread":
        return check_misread(args["value_a"], args["value_b"])
    return f"Unknown tool {name}"


def check_misread(a, b):
    """The model decides WHEN to compare; this code does the comparing (models are poor at digit checks)."""
    a, b = (str(v).replace(",", "").replace("$", "").strip() for v in (a, b))
    if a == b:
        return "Identical"
    if a.isdigit() and b.isdigit():                    # whole numbers: invoice ids
        if agent.one_digit_apart(a, b):
            return f"{a} and {b} differ by one digit: likely a misread of the same invoice number"
        return f"{a} and {b} are different invoice numbers"
    try:                                               # otherwise: amounts
        x, y = float(a), float(b)
    except ValueError:
        return "Different values"
    if agent.digits_swapped(x, y):
        return f"{x:,.2f} and {y:,.2f} are the same digits with two neighbours swapped: likely a misread"
    return f"Different amounts: {x:,.2f} vs {y:,.2f} (difference {abs(x - y):,.2f})"


# ---------------------------------------------------------------
# Step 3: the agent loop - ask the model, run its tool calls, repeat until it proposes
# ---------------------------------------------------------------
def investigate(ref, client=None):
    if client is None:
        import ollama                                  # only needed when this planner is used
        client = (ollama.Client(host="https://ollama.com", headers={"Authorization": f"Bearer {CLOUD_KEY}"})
                  if CLOUD_KEY else ollama)
    steps = []
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Investigate bank payment {ref}."}]

    for _ in range(MAX_STEPS):
        reply = client.chat(model=MODEL, messages=messages, tools=TOOLS,
                            options={"temperature": 0, "num_ctx": 6144})
        msg = reply.message
        messages.append(msg)
        calls = msg.tool_calls or []
        if not calls:                                  # the model talked instead of acting: nudge it once
            messages.append({"role": "user", "content": "Use the tools, then finish by calling propose."})
            continue
        for call in calls:
            name, args = call.function.name, dict(call.function.arguments or {})
            if name == "propose":
                return _finish(ref, steps, args)
            try:
                result = _run_tool(name, args)
            except Exception as e:                     # a bad argument is reported back, not fatal
                result = f"Error: {e}"
            text = json.dumps(result, default=str)
            steps.append({"tool": f"{name}({', '.join(f'{k}={v}' for k, v in args.items())})",
                          "finding": text[:300] + ("..." if len(text) > 300 else "")})
            messages.append({"role": "tool", "content": text, "tool_name": name})

    steps.append({"tool": "stop", "finding": f"No proposal after {MAX_STEPS} rounds"})
    return agent._result(ref, steps, "The model did not reach a conclusion", "chase", [], [], "Low",
                         "Ask the payer for a remittance advice (remittance chase)", planner="llm")


# ---------------------------------------------------------------
# Step 4: turn the model's proposal into ledger lines; the RULES work out the amounts
# ---------------------------------------------------------------
def _finish(ref, steps, args):
    action = (args.get("action") or "chase").strip().lower()
    hypothesis = args.get("hypothesis") or "(no explanation given)"
    confidence = (args.get("confidence") or "Low").strip().capitalize()
    steps.append({"tool": "propose", "finding": f"{action}: {args.get('invoice_ids') or '-'} ({confidence})"})
    if action not in ("match", "route", "chase"):
        action = "chase"

    lines, residuals, proposal = [], [], {"route": "Route to AP / treasury", "chase": "Ask the payer for a remittance advice"}.get(action)
    if action == "match":
        amount = round(agent.tool_payment(ref)["amount"], 2)
        ids = [i.strip() for i in (args.get("invoice_ids") or "").split(",") if i.strip()]
        invs = [agent.tool_invoice(i) for i in ids]
        if not ids or any(v is None for v in invs):
            return agent._result(ref, steps, hypothesis + " [Rejected by rules: unknown invoice]", "chase",
                                 [], [], "Low", "Ask the payer for a remittance advice", planner="llm")
        remaining = amount
        for inv in invs:                               # apply in the order given, never more than is open
            apply = round(min(inv["open_amount"], remaining), 2)
            lines.append((inv["invoice_id"], apply))
            remaining = round(remaining - apply, 2)
        last = invs[-1]
        short = round(last["open_amount"] - lines[-1][1], 2)
        if short > TOLERANCE:                          # a short pay: the rules open the residual item
            remit = agent.tool_remittance(ref)
            reason = next((r.deduction_reason for r in remit.itertuples() if r.invoice_id == last["invoice_id"]
                           and r.deduction_reason), None) or "Short pay - to investigate"
            rtype = "Balance due" if "partial" in reason.lower() else "Deduction"
            residuals.append((last["invoice_id"], last["customer_id"], short, rtype, reason, last["due_date"]))
        proposal = "Apply " + ", ".join(f"${a:,.2f} to {i}" for i, a in lines) + \
                   (f" and open a ${short:,.2f} {rtype.lower()} item" if residuals else "")
    return agent._result(ref, steps, hypothesis, action, lines, residuals, confidence, proposal, planner="llm")