-- OpsIntel analytics queries (MySQL 8).
-- Each block starts with "-- name: <query_name>" and is loaded by opsintel/queries.py.
-- {filters} is replaced by Python with fixed fragments such as "AND o.region IN :regions";
-- every user value is a bound parameter (:name), never pasted into the SQL text.


-- name: kpis
-- Headline numbers. CASE WHEN turns a condition into 1/0 so SUM() can count matching rows.
SELECT
    COUNT(*)                                                    AS orders,
    COALESCE(SUM(o.revenue), 0)                                 AS revenue,
    COALESCE(SUM(o.quantity), 0)                                AS units,
    COALESCE(AVG(o.revenue), 0)                                 AS avg_order_value,
    100 * SUM(CASE WHEN o.status = 'Delivered' THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
                                                                AS delivered_pct,
    100 * SUM(CASE WHEN o.status IN ('Cancelled', 'Returned') THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0)
                                                                AS cancel_return_pct,
    100 * SUM(CASE WHEN o.is_late = 1 THEN 1 ELSE 0 END) / NULLIF(COUNT(o.is_late), 0)
                                                                AS late_pct,
    AVG(o.delivery_days)                                        AS avg_delivery_days
FROM orders o
WHERE 1 = 1 {filters};


-- name: monthly_trend
-- CTE: first total each month, then work on those totals.
-- Window functions: LAG() reads the previous month's row (for month-on-month growth) and
-- SUM() OVER (ORDER BY ...) keeps a running total — without collapsing rows like GROUP BY does.
WITH monthly AS (
    SELECT o.order_month, SUM(o.revenue) AS revenue, COUNT(*) AS orders
    FROM orders o
    WHERE 1 = 1 {filters}
    GROUP BY o.order_month
)
SELECT
    order_month,
    revenue,
    orders,
    SUM(revenue) OVER (ORDER BY order_month)                          AS running_revenue,
    100 * (revenue - LAG(revenue) OVER (ORDER BY order_month))
        / NULLIF(LAG(revenue) OVER (ORDER BY order_month), 0)         AS growth_pct
FROM monthly
ORDER BY order_month;


-- name: category_ranking
-- RANK() OVER orders categories by revenue; SUM() OVER () with an empty window is the grand
-- total, so each category's share is computed in the same query.
SELECT
    o.category,
    SUM(o.revenue)                                              AS revenue,
    COUNT(*)                                                    AS orders,
    RANK() OVER (ORDER BY SUM(o.revenue) DESC)                  AS revenue_rank,
    100 * SUM(o.revenue) / SUM(SUM(o.revenue)) OVER ()          AS share_pct
FROM orders o
WHERE 1 = 1 {filters}
GROUP BY o.category
ORDER BY revenue_rank;


-- name: region_delivery
-- Delivery performance per region. HAVING filters groups (after GROUP BY), so regions with
-- no completed deliveries yet are left out instead of showing a misleading 0%.
SELECT
    o.region,
    SUM(o.revenue)                                                  AS revenue,
    COUNT(o.delivery_days)                                          AS deliveries,
    SUM(CASE WHEN o.is_late = 1 THEN 1 ELSE 0 END)                  AS late_deliveries,
    100 * SUM(CASE WHEN o.is_late = 1 THEN 1 ELSE 0 END) / COUNT(o.delivery_days) AS late_pct,
    AVG(o.delivery_days)                                            AS avg_delivery_days
FROM orders o
WHERE 1 = 1 {filters}
GROUP BY o.region
HAVING COUNT(o.delivery_days) > 0
ORDER BY late_pct DESC;


-- name: top_cities_per_region
-- "Top N per group": ROW_NUMBER() restarts at 1 for every region (PARTITION BY), ordered by
-- revenue, so keeping rn <= 3 gives the three best cities in each region.
WITH city_revenue AS (
    SELECT o.region, o.city, SUM(o.revenue) AS revenue, COUNT(*) AS orders
    FROM orders o
    WHERE 1 = 1 {filters}
    GROUP BY o.region, o.city
),
ranked AS (
    SELECT region, city, revenue, orders,
           ROW_NUMBER() OVER (PARTITION BY region ORDER BY revenue DESC) AS rn
    FROM city_revenue
)
SELECT region, rn AS city_rank, city, revenue, orders
FROM ranked
WHERE rn <= 3
ORDER BY region, rn;


-- name: status_breakdown
SELECT o.status, COUNT(*) AS orders
FROM orders o
WHERE 1 = 1 {filters}
GROUP BY o.status
ORDER BY orders DESC;


-- name: region_late_trend
-- For the insight "delivery delays rising": late % per region for each month.
-- AVG() OVER a sliding window of the 3 previous months gives each region its own baseline.
WITH monthly AS (
    SELECT o.region, o.order_month,
           100 * SUM(CASE WHEN o.is_late = 1 THEN 1 ELSE 0 END) / COUNT(o.delivery_days) AS late_pct,
           COUNT(o.delivery_days) AS deliveries
    FROM orders o
    GROUP BY o.region, o.order_month
    HAVING COUNT(o.delivery_days) >= 10
)
SELECT region, order_month, late_pct, deliveries,
       AVG(late_pct) OVER (PARTITION BY region ORDER BY order_month
                           ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS baseline_late_pct
FROM monthly
ORDER BY region, order_month;


-- name: category_month_trend
-- For the insight "demand surging/falling": each category's monthly revenue vs. its own
-- average over the previous 3 months.
WITH monthly AS (
    SELECT o.category, o.order_month, SUM(o.revenue) AS revenue
    FROM orders o
    GROUP BY o.category, o.order_month
)
SELECT category, order_month, revenue,
       AVG(revenue) OVER (PARTITION BY category ORDER BY order_month
                          ROWS BETWEEN 3 PRECEDING AND 1 PRECEDING) AS baseline_revenue
FROM monthly
ORDER BY category, order_month;


-- name: upload_history
-- INNER/LEFT JOIN: each upload with the number of orders it actually contributed (JOIN to
-- orders) and the number of problems recorded (JOIN to rejected_rows). LEFT JOIN keeps an
-- upload even if it has no problems (the count is then 0, not a missing row).
SELECT
    u.upload_id, u.file_name, u.uploaded_at, u.total_rows, u.loaded_rows, u.rejected_rows,
    u.duplicate_rows, u.fixed_values,
    100 * u.loaded_rows / NULLIF(u.total_rows, 0)   AS success_pct,
    COALESCE(o.orders_in_db, 0)                      AS orders_in_db,
    COALESCE(r.problems, 0)                          AS problems_recorded
FROM uploads u
LEFT JOIN (SELECT upload_id, COUNT(*) AS orders_in_db FROM orders GROUP BY upload_id) o
       ON o.upload_id = u.upload_id
LEFT JOIN (SELECT upload_id, COUNT(*) AS problems FROM rejected_rows GROUP BY upload_id) r
       ON r.upload_id = u.upload_id
ORDER BY u.uploaded_at DESC, u.upload_id DESC;


-- name: data_quality_totals
SELECT
    COUNT(*)                         AS uploads,
    COALESCE(SUM(total_rows), 0)     AS total_rows,
    COALESCE(SUM(loaded_rows), 0)    AS loaded_rows,
    COALESCE(SUM(rejected_rows), 0)  AS rejected_rows,
    COALESCE(SUM(duplicate_rows), 0) AS duplicate_rows,
    COALESCE(SUM(fixed_values), 0)   AS fixed_values
FROM uploads;


-- name: issues_by_column
-- Data-quality matrix for one upload: how many problems of each type in each column.
SELECT issue_column, issue_type, COUNT(*) AS problems
FROM rejected_rows
WHERE upload_id = :upload_id
GROUP BY issue_column, issue_type
ORDER BY problems DESC;


-- name: rejected_list
-- One row per refused line, with all its problems joined into one text (GROUP_CONCAT).
SELECT
    line_number,
    MAX(CASE WHEN issue_type = 'duplicate' THEN 'Duplicate' ELSE 'Invalid' END) AS kind,
    GROUP_CONCAT(message ORDER BY id SEPARATOR '; ')                            AS reasons,
    MIN(raw_data)                                                               AS original_row
FROM rejected_rows
WHERE upload_id = :upload_id
GROUP BY line_number
ORDER BY line_number;


-- name: records
SELECT o.order_id, o.order_date, o.region, o.state, o.city, o.category, o.product,
       o.quantity, o.unit_price, o.revenue, o.status, o.delivery_days, o.is_late, o.upload_id
FROM orders o
WHERE 1 = 1 {filters}
ORDER BY {order_by}
LIMIT :limit OFFSET :offset;


-- name: records_count
SELECT COUNT(*) AS total
FROM orders o
WHERE 1 = 1 {filters};


-- name: existing_order_ids
SELECT order_id FROM orders WHERE order_id IN :ids;


-- name: upload_by_hash
SELECT upload_id, file_name, uploaded_at FROM uploads WHERE file_hash = :file_hash;


-- name: upload_detail
SELECT * FROM uploads WHERE upload_id = :upload_id;


-- name: filter_options
SELECT
    (SELECT MIN(order_date) FROM orders) AS min_date,
    (SELECT MAX(order_date) FROM orders) AS max_date;
