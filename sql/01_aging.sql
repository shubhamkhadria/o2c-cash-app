-- AR aging report: open amount by how many days past due
WITH params AS (SELECT '2026-10-12' AS as_of),
aged AS (
  SELECT o.open_amount,
         julianday(p.as_of) - julianday(o.due_date) AS days_past_due
  FROM open_items o, params p
)
SELECT
  CASE
    WHEN days_past_due <= 0  THEN '1. Not yet due'
    WHEN days_past_due <= 30 THEN '2. 1-30 days'
    WHEN days_past_due <= 60 THEN '3. 31-60 days'
    WHEN days_past_due <= 90 THEN '4. 61-90 days'
    ELSE                          '5. 90+ days'
  END                                   AS bucket,
  COUNT(*)                              AS invoices,
  ROUND(SUM(open_amount), 2)            AS open_amount,
  ROUND(100.0 * SUM(open_amount) / (SELECT SUM(open_amount) FROM aged), 1) AS pct_of_ar
FROM aged
GROUP BY bucket
ORDER BY bucket;