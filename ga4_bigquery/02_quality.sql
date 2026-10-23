-- What the raw export looks like before anything is trusted: volumes, events
-- with no session, the debug flag, and the purchase events, which is where
-- this sample's data quality problems are.
WITH e AS (
  SELECT
    user_pseudo_id,
    event_name,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'debug_mode') AS debug_mode,
    event_date,
    ecommerce.transaction_id,
    ecommerce.purchase_revenue
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
),
p AS (
  SELECT
    transaction_id,
    purchase_revenue,
    transaction_id IS NOT NULL AND transaction_id != '(not set)' AS has_id
  FROM e
  WHERE event_name = 'purchase'
)
SELECT TO_JSON_STRING(STRUCT(
  (SELECT COUNT(*) FROM e) AS events,
  (SELECT COUNT(DISTINCT user_pseudo_id) FROM e) AS users,
  (SELECT COUNT(DISTINCT event_date) FROM e) AS days,
  (SELECT MIN(event_date) FROM e) AS first_day,
  (SELECT MAX(event_date) FROM e) AS last_day,
  (SELECT COUNT(DISTINCT CONCAT(user_pseudo_id, '.', CAST(ga_session_id AS STRING))) FROM e
     WHERE ga_session_id IS NOT NULL) AS sessions,
  (SELECT COUNTIF(event_name = 'session_start') FROM e) AS session_start_events,
  (SELECT COUNTIF(ga_session_id IS NULL) FROM e) AS events_without_session,
  (SELECT COUNTIF(debug_mode = 1) FROM e) AS debug_mode_events,
  (SELECT COUNT(*) FROM p) AS purchase_events,
  (SELECT COUNTIF(transaction_id IS NULL) FROM p) AS purchase_id_null,
  (SELECT COUNTIF(transaction_id = '(not set)') FROM p) AS purchase_id_not_set,
  (SELECT COUNT(DISTINCT transaction_id) FROM p WHERE has_id) AS transactions,
  (SELECT COUNTIF(has_id) - COUNT(DISTINCT IF(has_id, transaction_id, NULL)) FROM p) AS duplicate_purchase_events,
  (SELECT COUNT(*) FROM (SELECT transaction_id FROM p WHERE has_id GROUP BY 1 HAVING COUNT(*) > 1)) AS transactions_sent_more_than_once,
  (SELECT COUNTIF(purchase_revenue IS NULL) FROM p) AS purchase_revenue_null,
  (SELECT ROUND(SUM(purchase_revenue), 2) FROM p) AS revenue_summed_over_every_purchase_event
)) AS quality;
