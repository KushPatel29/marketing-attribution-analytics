-- One row per GA4 session, from Google's public GA4 export of the Google
-- Merchandise Store (bigquery-public-data.ga4_obfuscated_sample_ecommerce,
-- 1 Nov 2020 to 31 Jan 2021). Run in BigQuery; the sandbox is enough (it only
-- refuses INSERT/UPDATE/DELETE, and nothing here needs them).
--
-- Decisions a reader should know about:
--   * A session is (user_pseudo_id, ga_session_id). Events without a
--     ga_session_id belong to no session and are counted in 02_quality.sql.
--   * This export predates GA4's session traffic-source fields, so a session's
--     source/medium is the first source/medium event parameter seen in it, and
--     a session with none is Direct.
--   * A transaction is a distinct ecommerce.transaction_id. Only its first
--     purchase event counts; later events with the same ID are duplicates.
--     Purchase events with no ID ('(not set)' or NULL) cannot be deduplicated
--     and are kept apart, never mixed into transaction revenue.

CREATE SCHEMA IF NOT EXISTS ga4_portfolio OPTIONS (location = 'US');

CREATE OR REPLACE TABLE ga4_portfolio.sessions AS
WITH events AS (
  SELECT
    user_pseudo_id,
    (SELECT value.int_value FROM UNNEST(event_params) WHERE key = 'ga_session_id') AS ga_session_id,
    event_timestamp,
    event_name,
    device.category AS device_category,
    geo.country AS country,
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'source') AS source,
    (SELECT value.string_value FROM UNNEST(event_params) WHERE key = 'medium') AS medium,
    (SELECT COALESCE(value.string_value, CAST(value.int_value AS STRING))
       FROM UNNEST(event_params) WHERE key = 'session_engaged') AS session_engaged,
    traffic_source.source AS first_touch_source,
    traffic_source.medium AS first_touch_medium,
    ecommerce.transaction_id,
    ecommerce.purchase_revenue,
    (SELECT SUM(item_revenue) FROM UNNEST(items)) AS item_revenue
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
),
purchases AS (
  SELECT
    user_pseudo_id, ga_session_id, event_timestamp, transaction_id, purchase_revenue, item_revenue,
    transaction_id IS NOT NULL AND transaction_id != '(not set)' AS has_id,
    ROW_NUMBER() OVER (PARTITION BY transaction_id ORDER BY event_timestamp, user_pseudo_id) AS nth
  FROM events
  WHERE event_name = 'purchase' AND ga_session_id IS NOT NULL
),
session_purchases AS (
  SELECT
    user_pseudo_id, ga_session_id,
    COUNTIF(has_id AND nth = 1) AS transactions,
    COUNTIF(has_id AND nth > 1) AS duplicate_purchase_events,
    COUNTIF(NOT has_id) AS purchase_events_without_id,
    SUM(IF(has_id AND nth = 1, purchase_revenue, 0)) AS transaction_revenue,
    SUM(IF(has_id AND nth = 1, item_revenue, 0)) AS transaction_item_revenue,
    SUM(IF(NOT has_id, purchase_revenue, 0)) AS revenue_without_id
  FROM purchases
  GROUP BY 1, 2
),
sessions AS (
  SELECT
    user_pseudo_id,
    ga_session_id,
    MIN(event_timestamp) AS session_start_us,
    ANY_VALUE(device_category) AS device_category,
    ANY_VALUE(country) AS country,
    ARRAY_AGG(IF(source IS NULL, NULL, STRUCT(source, medium)) IGNORE NULLS
              ORDER BY event_timestamp LIMIT 1)[SAFE_OFFSET(0)] AS session_traffic,
    ANY_VALUE(first_touch_source) AS first_touch_source,
    ANY_VALUE(first_touch_medium) AS first_touch_medium,
    COUNT(*) AS events,
    COUNTIF(event_name = 'session_start') > 0 AS has_session_start,
    LOGICAL_OR(session_engaged = '1') AS engaged,
    MIN(IF(event_name = 'view_item', event_timestamp, NULL)) AS t_view_item,
    MIN(IF(event_name = 'add_to_cart', event_timestamp, NULL)) AS t_add_to_cart,
    MIN(IF(event_name = 'begin_checkout', event_timestamp, NULL)) AS t_begin_checkout,
    MIN(IF(event_name = 'add_shipping_info', event_timestamp, NULL)) AS t_add_shipping_info,
    MIN(IF(event_name = 'add_payment_info', event_timestamp, NULL)) AS t_add_payment_info,
    MIN(IF(event_name = 'purchase', event_timestamp, NULL)) AS t_purchase
  FROM events
  WHERE ga_session_id IS NOT NULL
  GROUP BY 1, 2
)
SELECT
  CONCAT(s.user_pseudo_id, '.', CAST(s.ga_session_id AS STRING)) AS session_key,
  s.user_pseudo_id,
  s.ga_session_id,
  TIMESTAMP_MICROS(s.session_start_us) AS session_start,
  DATE(TIMESTAMP_MICROS(s.session_start_us)) AS session_date,
  s.device_category,
  s.country,
  s.session_traffic.source AS session_source,
  s.session_traffic.medium AS session_medium,
  s.first_touch_source,
  s.first_touch_medium,
  -- A simplified GA4 default channel grouping. The sample obfuscates many
  -- sources as '<Other>' or '(data deleted)'; they get a channel of their own
  -- rather than a guess.
  CASE
    WHEN s.session_traffic IS NULL
      OR (s.session_traffic.source = '(direct)' AND s.session_traffic.medium IN ('(none)', '(not set)')) THEN 'Direct'
    WHEN s.session_traffic.medium = 'organic' THEN 'Organic Search'
    WHEN s.session_traffic.medium IN ('cpc', 'ppc', 'paid') THEN 'Paid Search'
    WHEN s.session_traffic.medium = 'referral' THEN 'Referral'
    WHEN s.session_traffic.medium = 'affiliate' THEN 'Affiliates'
    WHEN s.session_traffic.medium IN ('email', 'e-mail') THEN 'Email'
    WHEN s.session_traffic.source IN ('<Other>', '(data deleted)')
      OR s.session_traffic.medium IN ('<Other>', '(data deleted)') THEN 'Obfuscated in the sample'
    ELSE 'Other'
  END AS session_channel,
  CASE
    WHEN s.first_touch_source = '(direct)' AND s.first_touch_medium IN ('(none)', '(not set)') THEN 'Direct'
    WHEN s.first_touch_medium = 'organic' THEN 'Organic Search'
    WHEN s.first_touch_medium IN ('cpc', 'ppc', 'paid') THEN 'Paid Search'
    WHEN s.first_touch_medium = 'referral' THEN 'Referral'
    WHEN s.first_touch_medium = 'affiliate' THEN 'Affiliates'
    WHEN s.first_touch_medium IN ('email', 'e-mail') THEN 'Email'
    WHEN s.first_touch_source IN ('<Other>', '(data deleted)')
      OR s.first_touch_medium IN ('<Other>', '(data deleted)') THEN 'Obfuscated in the sample'
    ELSE 'Other'
  END AS first_touch_channel,
  s.events,
  s.has_session_start,
  COALESCE(s.engaged, FALSE) AS engaged,
  TIMESTAMP_MICROS(s.t_view_item) AS t_view_item,
  TIMESTAMP_MICROS(s.t_add_to_cart) AS t_add_to_cart,
  TIMESTAMP_MICROS(s.t_begin_checkout) AS t_begin_checkout,
  TIMESTAMP_MICROS(s.t_add_shipping_info) AS t_add_shipping_info,
  TIMESTAMP_MICROS(s.t_add_payment_info) AS t_add_payment_info,
  TIMESTAMP_MICROS(s.t_purchase) AS t_purchase,
  COALESCE(p.transactions, 0) AS transactions,
  COALESCE(p.duplicate_purchase_events, 0) AS duplicate_purchase_events,
  COALESCE(p.purchase_events_without_id, 0) AS purchase_events_without_id,
  COALESCE(p.transaction_revenue, 0) AS transaction_revenue,
  COALESCE(p.transaction_item_revenue, 0) AS transaction_item_revenue,
  COALESCE(p.revenue_without_id, 0) AS revenue_without_id
FROM sessions s
LEFT JOIN session_purchases p USING (user_pseudo_id, ga_session_id);

SELECT TO_JSON_STRING(STRUCT(
  COUNT(*) AS sessions,
  COUNT(DISTINCT user_pseudo_id) AS users,
  SUM(transactions) AS transactions,
  ROUND(SUM(transaction_revenue), 2) AS transaction_revenue)) AS built
FROM ga4_portfolio.sessions;
