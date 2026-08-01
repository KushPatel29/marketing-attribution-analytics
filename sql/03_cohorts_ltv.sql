-- Acquisition cohorts, retention, and cumulative revenue per acquired customer.
--
-- Cohort = the month of a customer's FIRST order, which is why is_first_order
-- exists on the fact rather than being inferred with a MIN() every time. The
-- retention triangle and the LTV curve are the same aggregate cut two ways.

DROP TABLE IF EXISTS customer_cohort;
CREATE TABLE customer_cohort AS
SELECT
    o.user_id,
    SUBSTR(MIN(CASE WHEN o.is_first_order = 1 THEN o.order_ts END), 1, 7) AS cohort_month,
    u.first_touch_channel,
    u.last_touch_channel,
    u.region
FROM fact_orders o
JOIN dim_user u ON u.user_id = o.user_id
GROUP BY o.user_id, u.first_touch_channel, u.last_touch_channel, u.region;


-- One row per cohort per month-since-acquisition.
DROP TABLE IF EXISTS cohort_activity;
CREATE TABLE cohort_activity AS
WITH ordered AS (
    SELECT
        c.cohort_month,
        c.user_id,
        SUBSTR(o.order_ts, 1, 7) AS order_month,
        o.revenue,
        o.gross_margin
    FROM fact_orders o
    JOIN customer_cohort c ON c.user_id = o.user_id
),
indexed AS (
    SELECT
        cohort_month,
        user_id,
        order_month,
        revenue,
        gross_margin,
        -- Whole months between cohort and order. Portable arithmetic on
        -- 'YYYY-MM' strings; a warehouse would use DATEDIFF(month, ...).
        (CAST(SUBSTR(order_month, 1, 4) AS INTEGER) * 12
            + CAST(SUBSTR(order_month, 6, 2) AS INTEGER))
        - (CAST(SUBSTR(cohort_month, 1, 4) AS INTEGER) * 12
            + CAST(SUBSTR(cohort_month, 6, 2) AS INTEGER)) AS months_since
    FROM ordered
)
SELECT
    cohort_month,
    months_since,
    COUNT(DISTINCT user_id) AS active_customers,
    COUNT(*)                AS orders,
    ROUND(SUM(revenue), 2)  AS revenue,
    ROUND(SUM(gross_margin), 2) AS gross_margin
FROM indexed
WHERE months_since >= 0
GROUP BY cohort_month, months_since
ORDER BY cohort_month, months_since;


-- The retention triangle: percentage of each cohort still ordering in month N.
DROP TABLE IF EXISTS cohort_retention;
CREATE TABLE cohort_retention AS
WITH size AS (
    SELECT cohort_month, COUNT(*) AS cohort_size
    FROM customer_cohort
    GROUP BY cohort_month
)
SELECT
    a.cohort_month,
    s.cohort_size,
    a.months_since,
    a.active_customers,
    ROUND(100.0 * a.active_customers / s.cohort_size, 2) AS retention_pct
FROM cohort_activity a
JOIN size s ON s.cohort_month = a.cohort_month
ORDER BY a.cohort_month, a.months_since;


-- Cumulative revenue per acquired customer — the LTV curve. Running total via
-- a window frame rather than a self-join, so it stays O(n).
DROP TABLE IF EXISTS cohort_ltv;
CREATE TABLE cohort_ltv AS
WITH size AS (
    SELECT cohort_month, COUNT(*) AS cohort_size
    FROM customer_cohort
    GROUP BY cohort_month
)
SELECT
    a.cohort_month,
    s.cohort_size,
    a.months_since,
    ROUND(SUM(a.revenue) OVER (
        PARTITION BY a.cohort_month
        ORDER BY a.months_since
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ), 2) AS cumulative_revenue,
    ROUND(SUM(a.revenue) OVER (
        PARTITION BY a.cohort_month
        ORDER BY a.months_since
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) / s.cohort_size, 2) AS cumulative_revenue_per_customer,
    ROUND(SUM(a.gross_margin) OVER (
        PARTITION BY a.cohort_month
        ORDER BY a.months_since
        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ) / s.cohort_size, 2) AS cumulative_margin_per_customer
FROM cohort_activity a
JOIN size s ON s.cohort_month = a.cohort_month
ORDER BY a.cohort_month, a.months_since;


-- Repeat behaviour by first-touch channel: does a channel bring back buyers,
-- or one-and-done traffic? Averaged over customers, not orders, so a single
-- heavy repeat buyer cannot carry a channel.
DROP TABLE IF EXISTS repeat_rate_by_channel;
CREATE TABLE repeat_rate_by_channel AS
WITH per_customer AS (
    SELECT
        c.first_touch_channel AS channel,
        c.user_id,
        COUNT(*) AS orders,
        SUM(o.revenue) AS revenue
    FROM fact_orders o
    JOIN customer_cohort c ON c.user_id = o.user_id
    GROUP BY c.first_touch_channel, c.user_id
)
SELECT
    channel,
    COUNT(*) AS customers,
    SUM(orders) AS orders,
    ROUND(100.0 * SUM(CASE WHEN orders > 1 THEN 1 ELSE 0 END) / COUNT(*), 2)
        AS repeat_rate_pct,
    ROUND(1.0 * SUM(orders) / COUNT(*), 3) AS orders_per_customer,
    ROUND(SUM(revenue) / COUNT(*), 2)      AS revenue_per_customer
FROM per_customer
GROUP BY channel
ORDER BY revenue_per_customer DESC;
