"""
generate_ledger.py  -  Week 2: build a synthetic, SAP-style AR ledger.

Run it with:   python generate_ledger.py

It (re)creates o2c.db with four tables and two views:
  customers            ~ SAP customer master (KNA1 / KNB1)
  invoices             ~ AR line items (in S/4HANA these live in the universal journal, ACDOCA)
  payments             ~ incoming bank payments (bank statement lines)
  payment_allocations  ~ which payment cleared which invoice = the "answer key" for our matcher later
  open_items  (view)   ~ like BSID in SAP: invoices not yet fully paid
  cleared_items (view) ~ like BSAD in SAP: invoices fully cleared
"""

import math
import random
import sqlite3
from collections import Counter
from datetime import date, timedelta

import pandas as pd

# =====================================================================
# YOUR BUSINESS DECISIONS  -  change any of these, then re-run the script
# =====================================================================
SEED = 42                      # same seed = exactly the same data every run
AS_OF = date(2026, 10, 12)     # "today" for the ledger
NUM_CUSTOMERS = 50
NUM_INVOICES = 1000
DAYS_OF_HISTORY = 365          # 12 months of invoices
AMOUNT_RANGE = (500, 50_000)   # invoice amount in USD

REGIONS = {"Midwest": 0.25, "Northeast": 0.25, "South": 0.25, "West": 0.25}
PAYMENT_TERMS = {30: 0.60, 45: 0.25, 60: 0.15}          # Net 30 / 45 / 60

# share of customers, and how many days after the due date they usually pay
PAYER_PROFILES = {
    "on_time":       {"share": 0.50, "days_late": (-5, 0)},
    "slightly_late": {"share": 0.30, "days_late": (1, 15)},
    "late":          {"share": 0.15, "days_late": (30, 75)},
    "at_risk":       {"share": 0.05, "days_late": (60, 150)},
}
AT_RISK_NEVER_PAYS = 0.40      # share of at-risk invoices that are never paid

PAYMENT_TYPES = {"full": 0.75, "multi_invoice": 0.10, "partial": 0.05, "short_pay": 0.10}
SHORT_PAY_REASONS = ["Pricing dispute", "Damaged goods", "Promotion / trade deduction", "Freight"]
REMITTANCE_QUALITY = {"correct": 0.70, "missing": 0.20, "wrong": 0.10}
BANK_NAME_VARIANT = 0.20       # share of payments where the bank shows a shortened payer name
# =====================================================================

random.seed(SEED)


def pick(weights):
    """Pick one key from a {option: probability} dictionary."""
    return random.choices(list(weights), weights=list(weights.values()))[0]


# ---------------------------------------------------------------------
# 1. Customers  (SAP: KNA1 general data, KNB1 company-code data like payment terms)
# ---------------------------------------------------------------------
PREFIX = ["Summit", "Harbor", "Prairie", "Granite", "Riverbend", "Bluewater", "Ironwood",
          "Cedar", "Lakeshore", "Pinnacle", "Redstone", "Silverline", "Northgate",
          "Evergreen", "Keystone", "Maple", "Horizon", "Copperfield", "Westbrook", "Oakridge"]
INDUSTRY = ["Foods", "Industrial Supply", "Logistics", "Healthcare", "Electric",
            "Building Products", "Packaging", "Auto Parts", "Chemicals", "Retail Group"]
SUFFIX = ["Inc.", "LLC", "Co.", "Corp."]

names = set()
while len(names) < NUM_CUSTOMERS:
    names.add(f"{random.choice(PREFIX)} {random.choice(INDUSTRY)} {random.choice(SUFFIX)}")

profile_shares = {p: v["share"] for p, v in PAYER_PROFILES.items()}
customers = []
for i, name in enumerate(sorted(names)):
    customers.append({
        "customer_id": str(100001 + i),                    # KUNNR
        "name": name,                                      # NAME1
        "region": pick(REGIONS),                           # REGIO
        "payment_terms_days": pick(PAYMENT_TERMS),         # ZTERM (e.g. NT30)
        "payer_profile": pick(profile_shares),             # not in SAP: our hidden "truth"
        "credit_limit": round(random.uniform(50_000, 500_000), -3),  # credit management
    })

# ---------------------------------------------------------------------
# 2. Invoices  (SAP: billing document posted to AR as an open item)
# ---------------------------------------------------------------------
# some customers buy much more than others
volume_weight = [random.uniform(0.3, 3.0) for _ in customers]
start = AS_OF - timedelta(days=DAYS_OF_HISTORY)
lo, hi = AMOUNT_RANGE

invoices = []
for _ in range(NUM_INVOICES):
    cust = random.choices(customers, weights=volume_weight)[0]
    inv_date = start + timedelta(days=random.randint(0, DAYS_OF_HISTORY - 1))
    amount = round(math.exp(random.uniform(math.log(lo), math.log(hi))), 2)  # many small, few large
    invoices.append({
        "customer_id": cust["customer_id"],
        "invoice_date": inv_date,                                     # BLDAT
        "due_date": inv_date + timedelta(days=cust["payment_terms_days"]),  # net due date
        "amount": amount,                                             # WRBTR
        "open_amount": amount,
        "status": "open",
        "cleared_date": None,                                         # AUGDT
        "clearing_doc": None,                                         # AUGBL
    })

# number invoices in date order, like an SAP number range
invoices.sort(key=lambda inv: inv["invoice_date"])
for n, inv in enumerate(invoices):
    inv["invoice_id"] = str(90001001 + n)                             # BELNR / VBELN

# decide WHEN each invoice gets paid, based on the customer's behavior
cust_by_id = {c["customer_id"]: c for c in customers}
for inv in invoices:
    profile = cust_by_id[inv["customer_id"]]["payer_profile"]
    if profile == "at_risk" and random.random() < AT_RISK_NEVER_PAYS:
        inv["pay_date"] = None
    else:
        low, high = PAYER_PROFILES[profile]["days_late"]
        inv["pay_date"] = inv["due_date"] + timedelta(days=random.randint(low, high))

# ---------------------------------------------------------------------
# 3. Payments and allocations  (SAP: incoming payment + clearing document)
# ---------------------------------------------------------------------
payments, allocations = [], []
type_counter, quality_counter = Counter(), Counter()


def bank_payer_name(name):
    """Banks often truncate or upper-case the payer name."""
    if random.random() < BANK_NAME_VARIANT:
        return name.upper().replace(".", "").replace(",", "")[:18].strip()
    return name


def remittance_reference(invoice_ids):
    """What the customer wrote as the payment reference."""
    quality = pick(REMITTANCE_QUALITY)
    quality_counter[quality] += 1
    if quality == "correct":
        return ", ".join(invoice_ids)
    if quality == "missing":
        return ""
    return str(int(invoice_ids[0]) + random.choice([-2, -1, 1, 2]))  # typo in the number


def create_payment(cust, pay_date, amount, invoice_ids):
    pay_id = f"PAY{len(payments) + 1:06d}"
    payments.append({
        "payment_id": pay_id,
        "payment_date": pay_date,
        "amount": round(amount, 2),
        "payer_name": bank_payer_name(cust["name"]),
        "remittance_ref": remittance_reference(invoice_ids),
        "bank_ref": f"BNK{random.randint(10**8, 10**9 - 1)}",
        "customer_id": cust["customer_id"],
    })
    return pay_id, f"14{len(payments):08d}"   # payment id, SAP-style clearing doc number


for cust in customers:
    paid = sorted(
        [inv for inv in invoices
         if inv["customer_id"] == cust["customer_id"]
         and inv["pay_date"] is not None and inv["pay_date"] <= AS_OF],
        key=lambda inv: inv["pay_date"],
    )
    i = 0
    while i < len(paid):
        inv = paid[i]
        ptype = pick(PAYMENT_TYPES)

        # one payment covering several invoices due around the same time
        if ptype == "multi_invoice":
            group = [inv]
            target = random.randint(2, 4)
            j = i + 1
            while j < len(paid) and len(group) < target and \
                    (paid[j]["pay_date"] - inv["pay_date"]).days <= 15:
                group.append(paid[j])
                j += 1
            if len(group) > 1:
                pay_date = max(g["pay_date"] for g in group)
                total = sum(g["amount"] for g in group)
                pay_id, clr = create_payment(cust, pay_date, total, [g["invoice_id"] for g in group])
                for g in group:
                    allocations.append({"payment_id": pay_id, "invoice_id": g["invoice_id"],
                                        "applied_amount": g["amount"], "short_pay_amount": 0.0,
                                        "short_pay_reason": None})
                    g.update(open_amount=0.0, status="cleared",
                             cleared_date=pay_date, clearing_doc=clr)
                type_counter["multi_invoice"] += 1
                i = j
                continue
            ptype = "full"   # nothing to group with, so treat it as a normal payment

        i += 1
        if ptype == "partial":
            paid_amt = round(inv["amount"] * random.uniform(0.4, 0.8), 2)
            pay_id, _ = create_payment(cust, inv["pay_date"], paid_amt, [inv["invoice_id"]])
            allocations.append({"payment_id": pay_id, "invoice_id": inv["invoice_id"],
                                "applied_amount": paid_amt, "short_pay_amount": 0.0,
                                "short_pay_reason": None})
            inv.update(open_amount=round(inv["amount"] - paid_amt, 2), status="partially_paid")

        elif ptype == "short_pay":
            deduction = round(inv["amount"] * random.uniform(0.02, 0.15), 2)
            reason = random.choice(SHORT_PAY_REASONS)
            pay_id, clr = create_payment(cust, inv["pay_date"], inv["amount"] - deduction,
                                         [inv["invoice_id"]])
            allocations.append({"payment_id": pay_id, "invoice_id": inv["invoice_id"],
                                "applied_amount": round(inv["amount"] - deduction, 2),
                                "short_pay_amount": deduction, "short_pay_reason": reason})
            # invoice is cleared; the deduction would go to dispute management
            inv.update(open_amount=0.0, status="cleared",
                       cleared_date=inv["pay_date"], clearing_doc=clr)

        else:  # full
            pay_id, clr = create_payment(cust, inv["pay_date"], inv["amount"], [inv["invoice_id"]])
            allocations.append({"payment_id": pay_id, "invoice_id": inv["invoice_id"],
                                "applied_amount": inv["amount"], "short_pay_amount": 0.0,
                                "short_pay_reason": None})
            inv.update(open_amount=0.0, status="cleared",
                       cleared_date=inv["pay_date"], clearing_doc=clr)
        type_counter[ptype] += 1

# ---------------------------------------------------------------------
# 4. Save to the database
# ---------------------------------------------------------------------
def to_frame(rows, columns):
    df = pd.DataFrame(rows)[columns]
    for col in df.columns:   # store dates as YYYY-MM-DD text
        df[col] = df[col].map(lambda v: v.isoformat() if isinstance(v, date) else v)
    return df


con = sqlite3.connect("o2c.db")
con.execute("DROP VIEW IF EXISTS open_items")
con.execute("DROP VIEW IF EXISTS cleared_items")

to_frame(customers, ["customer_id", "name", "region", "payment_terms_days",
                     "payer_profile", "credit_limit"]).to_sql("customers", con, if_exists="replace", index=False)
to_frame(invoices, ["invoice_id", "customer_id", "invoice_date", "due_date", "amount",
                    "open_amount", "status", "cleared_date", "clearing_doc"]).to_sql("invoices", con, if_exists="replace", index=False)
to_frame(payments, ["payment_id", "payment_date", "amount", "payer_name", "remittance_ref",
                    "bank_ref", "customer_id"]).to_sql("payments", con, if_exists="replace", index=False)
to_frame(allocations, ["payment_id", "invoice_id", "applied_amount", "short_pay_amount",
                       "short_pay_reason"]).to_sql("payment_allocations", con, if_exists="replace", index=False)

con.execute("CREATE VIEW open_items AS SELECT * FROM invoices WHERE status IN ('open', 'partially_paid')")
con.execute("CREATE VIEW cleared_items AS SELECT * FROM invoices WHERE status = 'cleared'")
con.commit()

# ---------------------------------------------------------------------
# 5. Summary  -  sanity-check that the data matches your decisions
# ---------------------------------------------------------------------
def pct(counter):
    total = sum(counter.values())
    return ", ".join(f"{k} {v / total:.0%}" for k, v in counter.most_common())


open_df = pd.read_sql("SELECT * FROM open_items", con)
print(f"Customers: {len(customers)}   Invoices: {len(invoices)}   Payments: {len(payments)}")
print(f"Invoice status:     {pct(Counter(inv['status'] for inv in invoices))}")
print(f"Payment types:      {pct(type_counter)}")
print(f"Remittance quality: {pct(quality_counter)}")
print(f"Open AR: ${open_df['open_amount'].sum():,.2f} across {len(open_df)} invoices")
print("\nSample open items:")
print(open_df[["invoice_id", "customer_id", "due_date", "amount", "open_amount", "status"]].head().to_string(index=False))
con.close()
