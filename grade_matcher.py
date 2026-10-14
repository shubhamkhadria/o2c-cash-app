"""
grade_matcher.py  -  Week 5: grade the matcher against the answer key.

Run it after matcher.py:   python grade_matcher.py
"""

import sqlite3

import pandas as pd

con = sqlite3.connect("o2c.db")
results = pd.read_sql("SELECT * FROM match_results", con)
truth = pd.read_sql("SELECT * FROM bank_answer_key", con)
con.close()

# the set of invoices each payment really pays, and the set the matcher proposed
true_sets = truth.groupby("bank_ref")["invoice_id"].apply(set).to_dict()
proposed = results.dropna(subset=["invoice_id"]).groupby("bank_ref")["invoice_id"].apply(set).to_dict()
status = results.drop_duplicates("bank_ref").set_index("bank_ref")[["status", "rule"]]

rows = []
for bank_ref, r in status.iterrows():
    actual = true_sets.get(bank_ref, set())          # empty = not a customer payment
    guess = proposed.get(bank_ref, set())
    if r["status"] == "Exception":
        correct = len(actual) == 0                   # right to reject only if nothing was owed
    else:
        correct = guess == actual
    rows.append({"bank_ref": bank_ref, "status": r["status"], "rule": r["rule"], "correct": correct})
graded = pd.DataFrame(rows)

total = len(graded)
auto = graded[graded["status"] == "Auto"]
review = graded[graded["status"] == "Review"]
exceptions = graded[graded["status"] == "Exception"]

print(f"Payments graded:    {total}")
print(f"Auto-applied:       {len(auto)} ({len(auto) / total:.0%})  accuracy {auto['correct'].mean():.0%}")
print(f"Sent to review:     {len(review)} ({len(review) / total:.0%})  suggestion correct {review['correct'].mean():.0%}")
print(f"Exceptions:         {len(exceptions)} ({len(exceptions) / total:.0%})  "
      f"of which genuinely unmatchable: {exceptions['correct'].sum()}")

wrong_auto = auto[~auto["correct"]]
if len(wrong_auto):
    print("\nWRONG auto-applications (cash posted to the wrong invoice):")
    print(wrong_auto.to_string(index=False))