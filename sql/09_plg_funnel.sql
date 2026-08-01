-- The product-led motion running alongside sales.
--
-- Self-serve signup, the activation milestone, the usage threshold that makes
-- an account product-qualified, and how long the first two take. Time-to-value
-- is the metric a PLG team actually manages, because activation rate is an
-- outcome and time-to-value is a lever.

DROP TABLE IF EXISTS plg_funnel;
CREATE TABLE plg_funnel AS
WITH steps AS (
    SELECT 1 AS step_order, 'signup'    AS step, COUNT(*) AS accounts FROM saas_fact_plg
    UNION ALL
    SELECT 2, 'activated', SUM(is_activated) FROM saas_fact_plg
    UNION ALL
    SELECT 3, 'product_qualified', SUM(is_pql) FROM saas_fact_plg
    UNION ALL
    -- The opportunity has to be created *after* the account qualified.
    -- Counting any opportunity the account ever had would score 100% here and
    -- mean nothing, because every account in this dataset has one.
    SELECT 4, 'became_opportunity', COUNT(DISTINCT p.account_id)
    FROM saas_fact_plg p
    JOIN saas_fact_opportunity o
      ON o.account_id = p.account_id
     AND o.created_date > p.pql_date
    WHERE p.is_pql = 1 AND p.pql_date <> ''
)
SELECT
    step_order,
    step,
    accounts,
    ROUND(100.0 * accounts
          / LAG(accounts) OVER (ORDER BY step_order), 2) AS step_conversion_pct,
    ROUND(100.0 * accounts
          / FIRST_VALUE(accounts) OVER (ORDER BY step_order), 2) AS pct_of_signups
FROM steps
ORDER BY step_order;


-- Time to value, by segment. The median matters more than the mean here — a
-- handful of accounts that activate after three months drag an average into
-- uselessness, and the median is what a product team can act on.
DROP TABLE IF EXISTS time_to_value;
CREATE TABLE time_to_value AS
WITH ranked AS (
    SELECT
        segment,
        days_to_value,
        ROW_NUMBER() OVER (PARTITION BY segment ORDER BY days_to_value) AS rn,
        COUNT(*)    OVER (PARTITION BY segment) AS n
    FROM saas_fact_plg
    WHERE is_activated = 1 AND days_to_value IS NOT NULL
)
SELECT
    segment,
    n AS activated_accounts,
    ROUND(AVG(days_to_value), 1) AS mean_days_to_value,
    -- Median without a percentile function: the middle row(s) by rank.
    ROUND(AVG(CASE WHEN rn IN ((n + 1) / 2, (n + 2) / 2) THEN days_to_value END), 1)
        AS median_days_to_value,
    MAX(days_to_value) AS slowest_days_to_value
FROM ranked
GROUP BY segment, n
ORDER BY median_days_to_value;


-- Does the product-led path produce better customers than the sales-led one?
-- The comparison every hybrid GTM team argues about, with the caveat that
-- self-serve accounts self-select and this is not a controlled comparison.
DROP TABLE IF EXISTS plg_vs_sales_led;
CREATE TABLE plg_vs_sales_led AS
WITH tagged AS (
    SELECT
        o.opportunity_id,
        o.amount_arr,
        o.is_won,
        o.is_closed,
        o.sales_cycle_days,
        CASE WHEN p.account_id IS NOT NULL AND p.is_pql = 1 THEN 'product-qualified'
             WHEN p.account_id IS NOT NULL THEN 'self-serve, not qualified'
             ELSE 'sales-led' END AS motion
    FROM saas_fact_opportunity o
    LEFT JOIN saas_fact_plg p ON p.account_id = o.account_id
)
SELECT
    motion,
    COUNT(*) AS opportunities,
    SUM(is_won) AS won,
    ROUND(100.0 * SUM(is_won) / NULLIF(SUM(is_closed), 0), 2) AS win_rate_pct,
    ROUND(AVG(amount_arr), 2) AS avg_deal_arr,
    ROUND(AVG(CASE WHEN is_closed = 1 THEN sales_cycle_days END), 1) AS avg_cycle_days,
    ROUND(SUM(CASE WHEN is_won = 1 THEN amount_arr ELSE 0 END), 2) AS won_arr
FROM tagged
GROUP BY motion
ORDER BY won_arr DESC;
