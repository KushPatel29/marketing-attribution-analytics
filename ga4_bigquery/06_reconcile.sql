-- The sessions table against the raw events, computed a second way with no
-- shared code: sessions, users, funnel steps, transactions and transaction
-- revenue must agree exactly, and the item-level revenue is compared with the
-- purchase-level revenue on the same transactions.
WITH e AS (
  SELECT
    user_pseudo_id,
    event_name,
    event_timestamp,
    CONCAT(user_pseudo_id, '.', CAST((SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS STRING)) AS session_key,
    ecommerce.transaction_id,
    ecommerce.purchase_revenue,
    (SELECT SUM(item_revenue) FROM UNNEST(items)) AS item_revenue
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
),
first_purchase AS (
  -- The first purchase event of each transaction, picked with ARRAY_AGG ...
  -- LIMIT 1 rather than the sessions build's ROW_NUMBER.
  SELECT transaction_id,
         ARRAY_AGG(STRUCT(purchase_revenue, item_revenue) ORDER BY event_timestamp, user_pseudo_id LIMIT 1)[OFFSET(0)] AS fp
  FROM e
  WHERE event_name = 'purchase' AND transaction_id IS NOT NULL AND transaction_id != '(not set)' AND session_key IS NOT NULL
  GROUP BY 1
),
from_events AS (
  SELECT AS STRUCT
    (SELECT COUNT(DISTINCT session_key) FROM e WHERE session_key IS NOT NULL) AS sessions,
    (SELECT COUNT(DISTINCT user_pseudo_id) FROM e WHERE session_key IS NOT NULL) AS users,
    (SELECT COUNT(DISTINCT IF(event_name = 'view_item', session_key, NULL)) FROM e) AS view_item,
    (SELECT COUNT(DISTINCT IF(event_name = 'add_to_cart', session_key, NULL)) FROM e) AS add_to_cart,
    (SELECT COUNT(DISTINCT IF(event_name = 'begin_checkout', session_key, NULL)) FROM e) AS begin_checkout,
    (SELECT COUNT(DISTINCT IF(event_name = 'purchase', session_key, NULL)) FROM e) AS purchase,
    (SELECT COUNT(*) FROM first_purchase) AS transactions,
    (SELECT ROUND(SUM(fp.purchase_revenue), 2) FROM first_purchase) AS transaction_revenue,
    (SELECT ROUND(SUM(fp.item_revenue), 2) FROM first_purchase) AS transaction_item_revenue,
    (SELECT COUNTIF(ABS(COALESCE(fp.purchase_revenue, 0) - COALESCE(fp.item_revenue, 0)) > 0.005) FROM first_purchase)
      AS transactions_where_items_disagree,
    (SELECT COUNTIF(fp.purchase_revenue IS NULL) FROM first_purchase) AS transactions_with_null_revenue
),
from_sessions AS (
  SELECT AS STRUCT
    COUNT(*) AS sessions,
    COUNT(DISTINCT user_pseudo_id) AS users,
    COUNTIF(t_view_item IS NOT NULL) AS view_item,
    COUNTIF(t_add_to_cart IS NOT NULL) AS add_to_cart,
    COUNTIF(t_begin_checkout IS NOT NULL) AS begin_checkout,
    COUNTIF(t_purchase IS NOT NULL) AS purchase,
    SUM(transactions) AS transactions,
    ROUND(SUM(transaction_revenue), 2) AS transaction_revenue,
    ROUND(SUM(transaction_item_revenue), 2) AS transaction_item_revenue
  FROM ga4_portfolio.sessions
)
SELECT TO_JSON_STRING(STRUCT(
  (SELECT * FROM from_events) AS from_events,
  (SELECT * FROM from_sessions) AS from_sessions
)) AS reconcile;
