-- The checkout funnel by session, two ways. Open: the session fired the step
-- at all. Closed: it fired every earlier step first, in order. A closed step
-- can never exceed its open count or the closed step before it.
WITH s AS (SELECT * FROM ga4_portfolio.sessions),
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
    IF(t_view_item IS NOT NULL, 1, 0) AS c1,
    IF(t_view_item <= t_add_to_cart, 1, 0) AS c2,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout, 1, 0) AS c3,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout
       AND t_begin_checkout <= t_add_shipping_info, 1, 0) AS c4,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout
       AND t_begin_checkout <= t_add_shipping_info AND t_add_shipping_info <= t_add_payment_info, 1, 0) AS c5,
    IF(t_view_item <= t_add_to_cart AND t_add_to_cart <= t_begin_checkout
       AND t_begin_checkout <= t_add_shipping_info AND t_add_shipping_info <= t_add_payment_info
       AND t_add_payment_info <= t_purchase, 1, 0) AS c6
  FROM s
)
SELECT TO_JSON_STRING(STRUCT(
  (SELECT AS STRUCT SUM(any_session) AS sessions, SUM(o1) AS view_item, SUM(o2) AS add_to_cart,
          SUM(o3) AS begin_checkout, SUM(o4) AS add_shipping_info, SUM(o5) AS add_payment_info,
          SUM(o6) AS purchase FROM steps) AS open_funnel,
  (SELECT AS STRUCT SUM(c1) AS view_item, SUM(c2) AS add_to_cart, SUM(c3) AS begin_checkout,
          SUM(c4) AS add_shipping_info, SUM(c5) AS add_payment_info, SUM(c6) AS purchase FROM steps) AS closed_funnel,
  (SELECT ARRAY_AGG(STRUCT(device_category, sessions, view_item, add_to_cart, purchase) ORDER BY sessions DESC)
     FROM (SELECT device_category, SUM(any_session) AS sessions, SUM(o1) AS view_item, SUM(o2) AS add_to_cart,
                  SUM(o6) AS purchase FROM steps GROUP BY 1)) AS open_by_device
)) AS funnel;
