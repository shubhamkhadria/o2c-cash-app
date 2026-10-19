O2C Cash Application — project guide for Claude Code
What this is

A working prototype of an AI cash application product for accounts receivable (order-to-cash). Bank payments come in, remittance advices are read by AI, the payer is identified, a rule-based matcher proposes how to apply the cash, a person approves, and the ledger is updated. Owner: Shubham (former HighRadius presales, PMP). Purpose: a credible, demo-ready portfolio project for finance systems, presales and AI program roles. He owns the design decisions.

How to run
Install: pip install -r requirements.txt
App: streamlit run Home.py (port 8501; in Codespaces use the Ports tab)
Always run scripts from the repo root (paths like o2c.db and data/ are relative to it).
Rebuild the data in this order: python generators/generate_ledger.py → python generators/make_bank_file.py → python pipeline/load_bank_file.py → python generators/make_remittances.py → python pipeline/extract_remittances.py → python generators/make_bank_accounts.py → python pipeline/load_bank_accounts.py → python pipeline/identify_payer.py → python pipeline/matcher.py → python tools/grade_matcher.py
Ad-hoc SQL: python tools/q.py sql/<file>.sql or sqlite3 o2c.db
Layout
Home.py, pages/ — Streamlit UI (manager dashboard, analyst workbench)
o2c/ — engine as importable functions (db.py, posting.py); new product logic goes here
pipeline/ — product steps run as scripts (load bank file, extract remittances, load bank accounts, identify payer, matcher); to be turned into o2c/ functions over time
generators/ — synthetic test data (ledger, bank file, remittances, bank accounts); not part of the product
tools/ — developer helpers (q.py, grade_matcher.py, confirm_payer.py, setup_check.py)
sql/ — reporting queries (aging, DSO, worklist, trends)
docs/matching_rules.md — matcher spec with change log (currently v1.2)
fixtures/ — committed test inputs (stub AI extractions)
data/ and *.db — generated, never committed
Business rules (do not change without asking)
Accuracy over automation rate. A wrong auto-posting costs far more than a manual review.
The matcher proposes; it never posts. Posting happens only through o2c/posting.py after a person accepts, or through the agreed Auto rules.
Short pays use the residual item process: clear the original invoice in full and create a new open item (<invoice>-R1) for the difference, keeping the original due date.
AI output is never trusted blindly: every extraction is validated against the ledger.
Learning a bank account needs a conflict check: if the account belongs to another customer, do not learn it; flag it.
Every posting is logged in decisions (who, when, what).
Security rules (never break)
Never store raw bank account numbers: keyed-hash fingerprint + last 4 digits only.
Secrets (BANK_HASH_KEY, any API key) live in Codespaces secrets. Never write them in code, files, commits or chat.
Never commit *.db, data/ or anything with real bank data. Check git status before committing.
Remittance extraction: EXTRACTOR = "stub" (prepared answers with two planted misreads, for testing the exceptions agent) or "ollama" (a real model reads each document) in pipeline/extract_remittances.py. Either way, validate() checks every answer against the ledger.
The LLM planner (o2c/llm_planner.py) calls Ollama Cloud (gpt-oss:20b) only when the OLLAMA_API_KEY Codespaces secret is set, and only on synthetic data. Without the key it uses local qwen2.5:3b. The model only proposes; guardrails and a person decide.