# O2C Cash Application (learning project)

An AI-assisted cash application prototype for order-to-cash (O2C), built on a synthetic, SAP-style AR ledger.
It reads bank payments, works out which open invoices they pay, and flags uncertain cases for review.

## Status
- [x] Synthetic AR ledger (customers, invoices, payments) modeled on SAP AR
- [x] AR reports in SQL: aging, DSO, collections worklist, payment trends
- [x] Bank statement loader that cleans a messy bank file
- [ ] Rule-based matcher with accuracy tests
- [ ] AI extraction from remittance documents
- [ ] Review screen and online demo

## How to run (GitHub Codespaces)
pip install pandas
python generate_ledger.py      # build the ledger in o2c.db
python make_bank_file.py       # create a sample bank statement
python load_bank_file.py       # clean it into the bank_payments table
python q.py sql/01_aging.sql   # run a report

All data is synthetic. No real customer data is used.