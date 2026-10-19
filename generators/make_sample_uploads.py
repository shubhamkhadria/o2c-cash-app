"""
generators/make_sample_uploads.py  -  Week 10: sample files for the Data Intake page.

Run it from the repo root:   python generators/make_sample_uploads.py

It exports today's synthetic customer master and open AR in two layouts a real customer might send:
  samples/simple_customer_master.csv   plain English headers ("Customer No", "Terms")  - easy for any planner
  samples/sap_customer_master.csv      SAP field names (KUNNR, NAME1, ZTERM ...)       - needs the AI
  samples/sap_open_items.xlsx          SAP open items (BELNR, KUNNR, BLDAT, FAEDT, WRBTR), German date format
The data is the same as in o2c.db, so loading it changes nothing except the layout it came from.
"""

import os
import sqlite3

import pandas as pd

os.makedirs("samples", exist_ok=True)
con = sqlite3.connect("o2c.db")
customers = pd.read_sql("SELECT * FROM customers ORDER BY customer_id", con)
open_items = pd.read_sql("SELECT * FROM open_items ORDER BY invoice_id", con)
con.close()

# 1. plain English export, e.g. from an accounting package
simple = pd.DataFrame({
    "Customer No": customers["customer_id"],
    "Customer Name": customers["name"],
    "Region": customers["region"],
    "Terms (days)": customers["payment_terms_days"],
    "Credit Limit": customers["credit_limit"].map(lambda v: f"${v:,.2f}"),
})
simple.to_csv("samples/simple_customer_master.csv", index=False)

# 2. SAP customer master (KNA1/KNB1 field names)
sap_customers = pd.DataFrame({
    "KUNNR": customers["customer_id"],
    "NAME1": customers["name"],
    "REGIO": customers["region"],
    "ZTERM": customers["payment_terms_days"].map(lambda d: f"NT{d}"),
    "KLIMK": customers["credit_limit"],
})
sap_customers.to_csv("samples/sap_customer_master.csv", index=False)

# 3. SAP open items (an FBL5N / BSID-style export): one amount per open item, dates as DD.MM.YYYY
german = lambda s: pd.to_datetime(s).dt.strftime("%d.%m.%Y")
sap_items = pd.DataFrame({
    "BUKRS": "1000",
    "BELNR": open_items["invoice_id"],
    "KUNNR": open_items["customer_id"],
    "BLDAT": german(open_items["invoice_date"]),
    "FAEDT": german(open_items["due_date"]),
    "WRBTR": open_items["open_amount"],
    "WAERS": "USD",
})
sap_items.to_excel("samples/sap_open_items.xlsx", index=False, sheet_name="BSID")

print(f"Wrote samples/: {len(customers)} customers (2 layouts), {len(open_items)} open items (SAP layout)")
