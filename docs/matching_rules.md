# Cash application matching rules

Version 1.1 · Owner: Shubham Khadria · Applies from week 5

## Purpose
Decide, for each customer payment in `bank_payments`, which open invoices it pays,
and whether that decision can be posted automatically, needs review, or is an exception.

## Inputs and outputs
- Inputs: `bank_payments` (cleaned bank file), `open_items` (open invoices)
- Outputs (proposals only; nothing is posted to the ledger):
  - `match_results`: payment, invoice, amount applied, rule, status
  - `proposed_residuals`: new residual items created by short or partial payments

## Settings
| Setting | Value |
| --- | --- |
| Amount tolerance | $1.00 |
| Short-pay threshold | 80% of the invoice amount |
| Residual due date | Same as the original invoice |

## Rules (applied in order; first rule that fits wins)
A reference is valid only if it is a real open invoice number in `open_items`.

| Rule | Condition | Action | Status |
| --- | --- | --- | --- |
| R1 Exact | Valid reference(s); payment equals the open amount (or sum) within $1.00 | Clear the invoice(s). A difference within tolerance goes to a small-differences account | Auto |
| R2 Deduction | One valid reference; payment is 80% or more of the open amount but short by more than $1.00 | Clear the invoice; create a residual item, type **Deduction**, reason "Short pay – to investigate", routed to dispute management | Auto |
| R3 Partial | One valid reference; payment is under 80% of the open amount | Clear the invoice; create a residual item, type **Balance due**, reason "Partial payment", stays with collections | Auto |
| R4 Multi-invoice short | Several valid references; payment is short of the sum by more than $1.00 | Suggest the match; a person decides how to split the difference | Review |
| R5 Overpayment | Valid reference(s); payment exceeds the open amount (or sum) by more than $1.00 | Suggest the match; the extra may be an unapplied credit | Review |
| R6 Amount only | No valid reference; amount equals exactly one open invoice within $1.00 | Suggest that invoice | Review |
| R7 No match | Anything else (including vendor refunds) | Send to the exception queue | Exception |

Residual items inherit the original invoice's due date, so aging and DSO stay accurate.

## Measures
- Auto-match rate: share of payments with status Auto
- Accuracy: share of Auto payments whose invoices match `bank_answer_key`
- Exception rate: share of payments with status Exception
- Accuracy matters more than auto-match rate.

## Out of scope for now
- Fixing mistyped references (week 7, fuzzy matching)
- Using payer names (week 7)
- Reading remittance PDFs and emails (week 6, AI extraction)

## Change log
| Version | Change | Reason |
| --- | --- | --- |
| 1.1 | R3 Partial moved from Auto to Review | A mistyped reference that belonged to another customer was auto-applied as a partial payment. Rework from wrong postings costs more than review. Revisit when payer-name checks exist (week 7) |
| 1.0 | First version | |git status