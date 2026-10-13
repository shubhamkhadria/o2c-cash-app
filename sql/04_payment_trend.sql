-- Who is paying slower? Average days late per customer per quarter, vs the previous quarter
WITH paid AS (
  SELECT customer_id,
         strftime('%Y', cleared_date) || '-Q' ||
           ((CAST(strftime('%m', cleared_date) AS INTEGER) + 2) / 3)  AS quarter,
         julianday(cleared_date) - julianday(due_date)                AS days_late
  FROM cleared_items
),
by_quarter AS (
  SELECT customer_id, quarter,
         COUNT(*)                  AS invoices_paid,
         ROUND(AVG(days_late), 1)  AS avg_days_late
  FROM paid
  GROUP BY customer_id, quarter
),
trend AS (
  SELECT customer_id, quarter, invoices_paid, avg_days_late,
         LAG(avg_days_late) OVER (PARTITION BY customer_id ORDER BY quarter) AS prev_quarter_days_late
  FROM by_quarter
)
SELECT c.name, t.quarter, t.invoices_paid, t.prev_quarter_days_late, t.avg_days_late,
       ROUND(t.avg_days_late - t.prev_quarter_days_late, 1) AS change_in_days
FROM trend t
JOIN customers c ON c.customer_id = t.customer_id
WHERE t.prev_quarter_days_late IS NOT NULL
ORDER BY change_in_days DESC
LIMIT 10;