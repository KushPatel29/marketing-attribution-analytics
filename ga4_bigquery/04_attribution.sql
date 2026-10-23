-- Who gets the credit for the store's revenue, under three rules a team
-- could defensibly pick, on real data with no planted truth to settle it.
--   first touch:            the channel that first acquired the user
--   last click:             the channel of the session that bought
--   last non-direct click:  as last click, but a Direct session hands the
--                           credit to the user's latest earlier non-direct
--                           session (the whole 92 days is the lookback)
-- Revenue is transaction revenue: one first purchase event per transaction ID.
WITH s AS (
  SELECT
    *,
    LAST_VALUE(IF(session_channel != 'Direct', session_channel, NULL) IGNORE NULLS)
      OVER (PARTITION BY user_pseudo_id ORDER BY session_start, ga_session_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS previous_non_direct
  FROM ga4_portfolio.sessions
),
credited AS (
  SELECT
    transaction_revenue,
    transactions,
    first_touch_channel,
    session_channel AS last_click_channel,
    IF(session_channel = 'Direct', COALESCE(previous_non_direct, 'Direct'), session_channel) AS last_non_direct_channel
  FROM s
  WHERE transactions > 0
),
by_rule AS (
  SELECT 'first_touch' AS rule, first_touch_channel AS channel, SUM(transaction_revenue) AS revenue, SUM(transactions) AS transactions
  FROM credited GROUP BY 1, 2
  UNION ALL
  SELECT 'last_click', last_click_channel, SUM(transaction_revenue), SUM(transactions) FROM credited GROUP BY 1, 2
  UNION ALL
  SELECT 'last_non_direct_click', last_non_direct_channel, SUM(transaction_revenue), SUM(transactions) FROM credited GROUP BY 1, 2
),
shares AS (
  SELECT rule, channel, revenue, transactions, revenue / SUM(revenue) OVER (PARTITION BY rule) AS share
  FROM by_rule
),
wide AS (
  SELECT
    channel,
    SUM(IF(rule = 'first_touch', share, 0)) AS ft,
    SUM(IF(rule = 'last_click', share, 0)) AS lc,
    SUM(IF(rule = 'last_non_direct_click', share, 0)) AS lndc
  FROM shares GROUP BY 1
)
SELECT TO_JSON_STRING(STRUCT(
  (SELECT ARRAY_AGG(STRUCT(rule, channel, ROUND(revenue, 2) AS revenue, transactions, ROUND(share, 4) AS share)
                    ORDER BY rule, revenue DESC) FROM shares) AS by_rule,
  (SELECT AS STRUCT
     ROUND(SUM(ABS(ft - lc)) / 2, 4) AS first_touch_vs_last_click,
     ROUND(SUM(ABS(lc - lndc)) / 2, 4) AS last_click_vs_last_non_direct,
     ROUND(SUM(ABS(ft - lndc)) / 2, 4) AS first_touch_vs_last_non_direct
   FROM wide) AS revenue_share_that_moves,
  (SELECT AS STRUCT ROUND(SUM(transaction_revenue), 2) AS revenue, SUM(transactions) AS transactions FROM credited) AS total
)) AS attribution;
