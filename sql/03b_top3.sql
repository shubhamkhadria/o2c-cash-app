-- Collections worklist: customers ranked by overdue amount within each region
WITH params AS (SELECT '2026-10-12' AS as_of),
overdue AS (
  SELECT c.region, c.customer_id, c.name,c.payer_profile,
         COUNT(*)                          AS overdue_invoices,
         ROUND(SUM(o.open_amount), 2)      AS overdue_amount,
         MAX(julianday(p.as_of) - julianday(o.due_date)) AS oldest_days_past_due
  FROM open_items o
  JOIN customers c ON c.customer_id = o.customer_id
  CROSS JOIN params p
  WHERE o.due_date < p.as_of
  GROUP BY c.region, c.customer_id, c.name
),
ranked AS (
  SELECT *,
         RANK() OVER (PARTITION BY region ORDER BY overdue_amount DESC) AS rnk
  FROM overdue
)
SELECT region, rnk, name, payer_profile, overdue_invoices, overdue_amount, oldest_days_past_due
FROM ranked
WHERE rnk <= 3
ORDER BY region, rnk;