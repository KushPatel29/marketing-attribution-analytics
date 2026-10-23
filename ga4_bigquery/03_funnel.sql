-- The checkout funnel by session, three ways.
--   open:     the session fired the step at all
--   strict:   it fired every earlier step first, in the order GA4 documents
--   as-tagged: strict, except that begin_checkout and add_shipping_info count
--             as one checkout stage reached at whichever fired first
-- The as-tagged version exists because this store's tags do not fire in the
-- documented order: in most sessions with both, add_shipping_info is logged
-- before begin_checkout (the ordering counts below show it). A strict funnel
-- would report a collapse at a step customers never actually abandon.
WITH s AS (
  SELECT *, LEAST(IFNULL(t_begin_checkout, t_add_shipping_info), IFNULL(t_add_shipping_info, t_begin_checkout)) AS t_checkout
  FROM ga4_portfolio.sessions
),
steps AS (
  SELECT
    device_category,
    1 AS any_session,
    IF(t_view_item IS NOT NULL, 1, 0) AS o1,
    IF(t_add_to_cart IS NOT NULL, 1, 0) AS o2,
    IF(t_begin_checkout IS NOT NULL, 1, 0) AS o3,
    IF(t_add_shipping_info IS NOT NULL, 1, 0) AS o4,
    IF(t_add_payment_info IS NOT NULL, 1, 0) AS o5,
    IF(t_purchase IS NOT NULL, 1, 0) AS o6,
    IF(t_view_item <= t_add_to_cart, 1, 0) AS c2,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout, 1, 0) AS c3,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout
       AND t_begin_checkout <= t_add_shipping_info, 1, 0) AS c4,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout
       AND t_begin_checkout <= t_add_shipping_info AND t_add_shipping_info <= t_add_payment_info, 1, 0) AS c5,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout
       AND t_begin_checkout <= t_add_shipping_info AND t_add_shipping_info <= t_add_payment_info
       AND t_add_payment_info <= t_purchase, 1, 0) AS c6,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_checkout, 1, 0) AS a3,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_checkout AND t_checkout <= t_add_payment_info, 1, 0) AS a5,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_checkout AND t_checkout <= t_add_payment_info
       AND t_add_payment_info <= t_purchase, 1, 0) AS a6,
    IF(t_add_to_cart IS NULL AND t_checkout IS NOT NULL, 1, 0) AS checkout_without_cart,
    IF(t_add_shipping_info < t_begin_checkout, 1, 0) AS shipping_logged_first,
    IF(t_add_shipping_info IS NOT NULL AND t_begin_checkout IS NOT NULL, 1, 0) AS both_checkout_events
  FROM s
)
SELECT TO_JSON_STRING(STRUCT(
  (SELECT AS STRUCT SUM(any_session) AS sessions, SUM(o1) AS view_item, SUM(o2) AS add_to_cart,
          SUM(o3) AS begin_checkout, SUM(o4) AS add_shipping_info, SUM(o5) AS add_payment_info,
          SUM(o6) AS purchase FROM steps) AS open_funnel,
  (SELECT AS STRUCT SUM(o1) AS view_item, SUM(c2) AS add_to_cart, SUM(c3) AS begin_checkout,
          SUM(c4) AS add_shipping_info, SUM(c5) AS add_payment_info, SUM(c6) AS purchase FROM steps) AS strict_funnel,
  (SELECT AS STRUCT SUM(o1) AS view_item, SUM(c2) AS add_to_cart, SUM(a3) AS checkout,
          SUM(a5) AS add_payment_info, SUM(a6) AS purchase FROM steps) AS as_tagged_funnel,
  (SELECT AS STRUCT SUM(both_checkout_events) AS sessions_with_both_checkout_events,
          SUM(shipping_logged_first) AS shipping_logged_before_begin_checkout,
          SUM(checkout_without_cart) AS checkout_with_no_add_to_cart_in_session FROM steps) AS tagging_order,
  (SELECT ARRAY_AGG(STRUCT(device_category, sessions, view_item, add_to_cart, purchase) ORDER BY sessions DESC)
     FROM (SELECT device_category, SUM(any_session) AS sessions, SUM(o1) AS view_item, SUM(o2) AS add_to_cart,
                  SUM(o6) AS purchase FROM steps GROUP BY 1)) AS open_by_device
)) AS funnel;
