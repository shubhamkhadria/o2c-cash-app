"""
make_bank_accounts.py  -  Week 7: create customer bank account data (fictional numbers).
 
Run it with:   python make_bank_accounts.py
 
Writes two raw files into data/ (never committed):
  data/onboarding_bank_accounts.csv  ~ accounts collected when customers were set up (like SAP KNBK)
  data/payment_payer_accounts.csv    ~ the account each bank payment came from (like MICR / ACH originator data)
"""
 
import os
import random
import re
import sqlite3
 
import pandas as pd
 
SEED = 21
SECOND_ACCOUNT = 0.30      # share of customers with a second account
USE_GROUP_ACCOUNT = 0.40   # when a customer belongs to a group, how often they pay from the group's shared account
USE_NEW_ACCOUNT = 0.15     # how often a customer pays from an account we have never seen
ROUTING_NUMBERS = ["071000013", "021000021", "111000614", "121000248", "061000104", "044000037"]  # fictional for this project
 
random.seed(SEED)
con = sqlite3.connect("o2c.db")
customers = pd.read_sql("SELECT customer_id, name FROM customers ORDER BY customer_id", con)
payer_of_payment = pd.read_sql("""
    SELECT DISTINCT k.bank_ref, i.customer_id
    FROM bank_answer_key k JOIN invoices i USING (invoice_id)
""", con)
payments = pd.read_sql("SELECT bank_ref FROM bank_payments", con)
con.close()
 
 
def new_account():
    return random.choice(ROUTING_NUMBERS), str(random.randint(10**7, 10**11))
 
 
def group_name(name):
    """Customers with the same core name belong to one corporate group."""
    words = re.sub(r"[^A-Z ]", "", name.upper()).split()
    return " ".join(w for w in words if w not in {"INC", "LLC", "CO", "CORP"})
 
 
# accounts collected at onboarding
master, own_accounts = [], {}
for c in customers.itertuples():
    accounts = [new_account()]
    if random.random() < SECOND_ACCOUNT:
        accounts.append(new_account())
    own_accounts[c.customer_id] = accounts
    for routing, account in accounts:
        master.append({"customer_id": c.customer_id, "routing_number": routing, "account_number": account})
 
# one shared treasury account per corporate group (two or more customers with the same core name)
customers["group"] = customers["name"].map(group_name)
group_accounts = {}
for group, members in customers.groupby("group"):
    if len(members) > 1:
        group_accounts[group] = new_account()
        for cid in members["customer_id"]:
            master.append({"customer_id": cid, "routing_number": group_accounts[group][0],
                           "account_number": group_accounts[group][1]})
 
# which account each payment came from
group_of = customers.set_index("customer_id")["group"].to_dict()
payer_lookup = payer_of_payment.set_index("bank_ref")["customer_id"].to_dict()
payment_accounts = []
for ref in payments["bank_ref"]:
    cid = payer_lookup.get(ref)
    roll = random.random()
    if cid is None or roll < USE_NEW_ACCOUNT:
        routing, account = new_account()                      # unknown account (or not a customer at all)
    elif group_of[cid] in group_accounts and roll < USE_NEW_ACCOUNT + USE_GROUP_ACCOUNT:
        routing, account = group_accounts[group_of[cid]]      # paid from the shared group account
    else:
        routing, account = random.choice(own_accounts[cid])   # one of the customer's own accounts
    payment_accounts.append({"bank_ref": ref, "routing_number": routing, "account_number": account})
 
os.makedirs("data", exist_ok=True)
pd.DataFrame(master).to_csv("data/onboarding_bank_accounts.csv", index=False)
pd.DataFrame(payment_accounts).to_csv("data/payment_payer_accounts.csv", index=False)
print(f"Wrote {len(master)} onboarding accounts for {len(customers)} customers "
      f"({len(group_accounts)} shared group accounts) and payer accounts for {len(payment_accounts)} payments")