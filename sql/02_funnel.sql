-- Funnel: where sessions die, overall and by device.
--
-- Step conversion is deliberately computed with LAG over an ordered step index
-- rather than by hand-writing five subqueries, so adding a funnel step means
-- adding a row of data, not editing this file.

DROP TABLE IF EXISTS funnel_overall;
CREATE TABLE funnel_overall AS
WITH step_sessions AS (
    SELECT
        funnel_step,
        event_name,
        COUNT(DISTINCT session_id) AS sessions
    FROM fact_events
    GROUP BY funnel_step, event_name
)
SELECT
    funnel_step,
    event_name,
    sessions,
    LAG(sessions) OVER (ORDER BY funnel_step) AS prev_step_sessions,
    ROUND(100.0 * sessions
          / LAG(sessions) OVER (ORDER BY funnel_step), 2) AS step_conversion_pct,
    ROUND(100.0 * sessions
          / FIRST_VALUE(sessions) OVER (ORDER BY funnel_step), 2) AS pct_of_entry,
    LAG(sessions) OVER (ORDER BY funnel_step) - sessions AS sessions_lost
FROM step_sessions
ORDER BY funnel_step;


-- Same funnel split by device. The checkout step is the one that moves, which
-- is the finding the whole table exists to make visible: mobile carries most
-- of the traffic and loses a disproportionate share of it at checkout.
DROP TABLE IF EXISTS funnel_by_device;
CREATE TABLE funnel_by_device AS
WITH step_sessions AS (
    SELECT
        device,
        funnel_step,
        event_name,
        COUNT(DISTINCT session_id) AS sessions
    FROM fact_events
    GROUP BY device, funnel_step, event_name
)
SELECT
    device,
    funnel_step,
    event_name,
    sessions,
    ROUND(100.0 * sessions
          / LAG(sessions) OVER (PARTITION BY device ORDER BY funnel_step), 2)
        AS step_conversion_pct,
    ROUND(100.0 * sessions
          / FIRST_VALUE(sessions) OVER (PARTITION BY device ORDER BY funnel_step), 2)
        AS pct_of_entry
FROM step_sessions
ORDER BY device, funnel_step;


-- The single number a product manager would act on: how much worse is mobile
-- at the checkout step than the best device, in percentage points.
DROP TABLE IF EXISTS funnel_device_gap;
CREATE TABLE funnel_device_gap AS
WITH checkout_rate AS (
    SELECT
        device,
        step_conversion_pct AS checkout_start_rate
    FROM funnel_by_device
    WHERE event_name = 'checkout_start'
)
SELECT
    device,
    checkout_start_rate,
    ROUND(MAX(checkout_start_rate) OVER () - checkout_start_rate, 2)
        AS points_behind_best_device
FROM checkout_rate
ORDER BY checkout_start_rate DESC;


-- Entry-channel quality. A channel that sends traffic which never reaches a
-- product view is buying sessions, not customers.
DROP TABLE IF EXISTS funnel_by_entry_channel;
CREATE TABLE funnel_by_entry_channel AS
WITH per_session AS (
    SELECT
        s.session_id,
        s.channel,
        MAX(e.funnel_step) AS deepest_step
    FROM fact_sessions s
    JOIN fact_events e ON e.session_id = s.session_id
    GROUP BY s.session_id, s.channel
)
SELECT
    channel,
    COUNT(*) AS sessions,
    SUM(CASE WHEN deepest_step >= 2 THEN 1 ELSE 0 END) AS reached_product_view,
    SUM(CASE WHEN deepest_step >= 3 THEN 1 ELSE 0 END) AS reached_add_to_cart,
    SUM(CASE WHEN deepest_step >= 5 THEN 1 ELSE 0 END) AS reached_purchase,
    ROUND(100.0 * SUM(CASE WHEN deepest_step >= 2 THEN 1 ELSE 0 END) / COUNT(*), 2)
        AS product_view_rate_pct,
    ROUND(100.0 * SUM(CASE WHEN deepest_step >= 5 THEN 1 ELSE 0 END) / COUNT(*), 2)
        AS session_conversion_rate_pct
FROM per_session
GROUP BY channel
ORDER BY session_conversion_rate_pct DESC;
