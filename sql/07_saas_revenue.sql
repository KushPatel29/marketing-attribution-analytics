-- SaaS revenue: the ARR waterfall, and the retention metrics built on it.
--
-- Every number here is an aggregation of the movement ledger. That is the
-- whole design argument: a balance tells you where ARR ended, and nothing
-- about how it got there. New, expansion, contraction and churn are four
-- different businesses with four different owners, and only the ledger can
-- separate them.

DROP TABLE IF EXISTS arr_waterfall;
CREATE TABLE arr_waterfall AS
WITH monthly AS (
    SELECT
        movement_month,
        SUM(CASE WHEN movement_type = 'new'         THEN arr_delta ELSE 0 END) AS new_arr,
        SUM(CASE WHEN movement_type = 'expansion'   THEN arr_delta ELSE 0 END) AS expansion_arr,
        SUM(CASE WHEN movement_type = 'contraction' THEN arr_delta ELSE 0 END) AS contraction_arr,
        SUM(CASE WHEN movement_type = 'churn'       THEN arr_delta ELSE 0 END) AS churn_arr,
        SUM(arr_delta) AS net_new_arr
    FROM saas_fact_arr_movement
    GROUP BY movement_month
)
SELECT
    movement_month,
    ROUND(new_arr, 2)          AS new_arr,
    ROUND(expansion_arr, 2)    AS expansion_arr,
    ROUND(contraction_arr, 2)  AS contraction_arr,
    ROUND(churn_arr, 2)        AS churn_arr,
    ROUND(net_new_arr, 2)      AS net_new_arr,
    -- Ending ARR is the running total of everything that has moved.
    ROUND(SUM(net_new_arr) OVER (
        ORDER BY movement_month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
    ), 2) AS ending_arr,
    -- Quick ratio: growth bought per dollar lost. Below 1 the book is shrinking.
    ROUND((new_arr + expansion_arr)
          / NULLIF(-1 * (contraction_arr + churn_arr), 0), 2) AS quick_ratio
FROM monthly
ORDER BY movement_month;


-- Net and gross revenue retention on the *existing* book.
--
-- The distinction people get wrong: NRR counts expansion, GRR does not. A
-- business can post 105% NRR while losing a fifth of its customers, because a
-- handful of accounts grew enough to paper over it — which is exactly why both
-- belong on the same row.
DROP TABLE IF EXISTS revenue_retention;
CREATE TABLE revenue_retention AS
WITH base AS (
    -- ARR belonging to subscriptions that existed before the movement month.
    SELECT
        m.movement_month,
        m.segment,
        SUM(CASE WHEN m.movement_type = 'expansion'   THEN m.arr_delta ELSE 0 END) AS expansion,
        SUM(CASE WHEN m.movement_type = 'contraction' THEN -m.arr_delta ELSE 0 END) AS contraction,
        SUM(CASE WHEN m.movement_type = 'churn'       THEN -m.arr_delta ELSE 0 END) AS churned,
        -- Starting ARR for the cohort of subscriptions that moved this month.
        SUM(CASE WHEN m.movement_type IN ('expansion', 'contraction', 'churn')
                 THEN m.arr_after - m.arr_delta ELSE 0 END) AS starting_arr
    FROM saas_fact_arr_movement m
    WHERE m.movement_type <> 'new'
    GROUP BY m.movement_month, m.segment
)
SELECT
    movement_month,
    segment,
    ROUND(starting_arr, 2)  AS starting_arr,
    ROUND(expansion, 2)     AS expansion_arr,
    ROUND(contraction, 2)   AS contraction_arr,
    ROUND(churned, 2)       AS churned_arr,
    ROUND(100.0 * (starting_arr + expansion - contraction - churned)
          / NULLIF(starting_arr, 0), 2) AS nrr_pct,
    ROUND(100.0 * (starting_arr - contraction - churned)
          / NULLIF(starting_arr, 0), 2) AS grr_pct
FROM base
WHERE starting_arr > 0
ORDER BY movement_month, segment;


-- The same two numbers for the whole period, which is how they get quoted.
DROP TABLE IF EXISTS retention_summary;
CREATE TABLE retention_summary AS
WITH agg AS (
    SELECT
        segment,
        SUM(CASE WHEN movement_type = 'expansion'   THEN arr_delta ELSE 0 END) AS expansion,
        SUM(CASE WHEN movement_type = 'contraction' THEN -arr_delta ELSE 0 END) AS contraction,
        SUM(CASE WHEN movement_type = 'churn'       THEN -arr_delta ELSE 0 END) AS churned,
        SUM(CASE WHEN movement_type IN ('expansion','contraction','churn')
                 THEN arr_after - arr_delta ELSE 0 END) AS starting_arr,
        SUM(CASE WHEN movement_type = 'churn' THEN 1 ELSE 0 END) AS logos_churned,
        COUNT(DISTINCT CASE WHEN movement_type IN ('expansion','contraction','churn')
                            THEN subscription_id END) AS logos_at_risk
    FROM saas_fact_arr_movement
    GROUP BY segment
)
SELECT
    segment,
    ROUND(starting_arr, 2) AS starting_arr,
    ROUND(100.0 * (starting_arr + expansion - contraction - churned)
          / NULLIF(starting_arr, 0), 2) AS nrr_pct,
    ROUND(100.0 * (starting_arr - contraction - churned)
          / NULLIF(starting_arr, 0), 2) AS grr_pct,
    logos_at_risk,
    logos_churned,
    -- Logo churn and dollar churn diverge whenever churn is concentrated in
    -- small accounts, which it almost always is.
    ROUND(100.0 * logos_churned / NULLIF(logos_at_risk, 0), 2) AS logo_churn_pct,
    ROUND(100.0 * churned / NULLIF(starting_arr, 0), 2)        AS dollar_churn_pct
FROM agg
ORDER BY starting_arr DESC;


-- New ARR by acquisition channel: the join back to act one. The attribution
-- model chosen upstream decides which channel is credited here, which is how
-- an argument about methodology becomes an argument about budget.
DROP TABLE IF EXISTS new_arr_by_channel;
CREATE TABLE new_arr_by_channel AS
SELECT
    o.source_channel AS channel,
    COUNT(*) AS won_deals,
    ROUND(SUM(o.amount_arr), 2) AS new_arr,
    ROUND(AVG(o.amount_arr), 2) AS avg_deal_arr,
    ROUND(100.0 * SUM(o.amount_arr) / SUM(SUM(o.amount_arr)) OVER (), 4) AS pct_of_new_arr
FROM saas_fact_opportunity o
WHERE o.is_won = 1
GROUP BY o.source_channel
ORDER BY new_arr DESC;
