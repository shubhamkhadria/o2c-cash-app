-- DSO = open AR today / credit sales in the last 90 days x 90
WITH params AS (SELECT '2026-10-12' AS as_of, 90 AS days),
ar AS (SELECT SUM(open_amount) AS open_ar FROM open_items),
sales AS (
  SELECT SUM(i.amount) AS sales_90d
  FROM invoices i, params p
  WHERE i.invoice_date > date(p.as_of, '-' || p.days || ' days')
)
SELECT ROUND(ar.open_ar, 2)                      AS open_ar,
       ROUND(sales.sales_90d, 2)                 AS sales_last_90d,
       ROUND(ar.open_ar / sales.sales_90d * p.days, 1) AS dso_days
FROM ar, sales, params p;