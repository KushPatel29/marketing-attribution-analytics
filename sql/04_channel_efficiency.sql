-- Channel efficiency the way almost every marketing dashboard computes it:
-- cost from the spend table, conversions from whichever session closed the
-- journey. This is the default view, and the rest of the repo exists to show
-- how far wrong it is.
--
-- Note the aggregate-then-join shape. Spend is at (date, channel) and sessions
-- are at (session), so joining them directly fans cost out across every
-- session and inflates spend by four orders of magnitude. Both sides are
-- reduced to one row per channel *before* they meet.

DROP TABLE IF EXISTS channel_spend_rollup;
CREATE TABLE channel_spend_rollup AS
SELECT
    channel,
    SUM(impressions) AS impressions,
    SUM(clicks)      AS clicks,
    ROUND(SUM(spend), 2) AS spend
FROM fact_spend
GROUP BY channel;

DROP TABLE IF EXISTS channel_session_rollup;
CREATE TABLE channel_session_rollup AS
SELECT
    channel,
    COUNT(*) AS sessions,
    SUM(is_converting_session) AS last_touch_conversions
FROM fact_sessions
GROUP BY channel;

-- Revenue credited to the channel that closed the journey.
DROP TABLE IF EXISTS channel_revenue_rollup;
CREATE TABLE channel_revenue_rollup AS
SELECT
    s.channel,
    ROUND(SUM(o.revenue), 2)      AS revenue,
    ROUND(SUM(o.gross_margin), 2) AS gross_margin,
    COUNT(*)                      AS orders
FROM fact_orders o
JOIN fact_sessions s ON s.session_id = o.session_id
GROUP BY s.channel;


DROP TABLE IF EXISTS channel_efficiency;
CREATE TABLE channel_efficiency AS
SELECT
    ss.channel,
    d.channel_group,
    d.is_paid,
    sp.impressions,
    sp.clicks,
    sp.spend,
    ss.sessions,
    ss.last_touch_conversions,
    COALESCE(r.orders, 0)       AS orders,
    COALESCE(r.revenue, 0)      AS revenue,
    COALESCE(r.gross_margin, 0) AS gross_margin,
    ROUND(100.0 * sp.clicks / NULLIF(sp.impressions, 0), 3) AS ctr_pct,
    ROUND(sp.spend / NULLIF(sp.clicks, 0), 2)               AS effective_cpc,
    ROUND(100.0 * ss.last_touch_conversions / NULLIF(ss.sessions, 0), 2)
        AS session_conversion_pct,
    -- Cost per acquisition, on last-touch credit. NULL for unpaid channels
    -- rather than 0, because "free" and "we did not measure it" are different.
    CASE WHEN d.is_paid = 1
         THEN ROUND(sp.spend / NULLIF(ss.last_touch_conversions, 0), 2)
    END AS cpa_last_touch,
    CASE WHEN d.is_paid = 1
         THEN ROUND(COALESCE(r.revenue, 0) / NULLIF(sp.spend, 0), 2)
    END AS roas_last_touch,
    -- Share of the total last-touch conversion pool. This column is what the
    -- attribution bake-off is graded against.
    ROUND(1.0 * ss.last_touch_conversions
          / SUM(ss.last_touch_conversions) OVER (), 6) AS last_touch_share
FROM channel_session_rollup ss
JOIN dim_channel d           ON d.channel = ss.channel
LEFT JOIN channel_spend_rollup sp   ON sp.channel = ss.channel
LEFT JOIN channel_revenue_rollup r  ON r.channel = ss.channel
ORDER BY ss.last_touch_conversions DESC;


-- Monthly trend, so a reader can see that the misattribution is not a
-- one-month artefact.
DROP TABLE IF EXISTS channel_monthly;
CREATE TABLE channel_monthly AS
WITH spend_m AS (
    SELECT dt.month_start, sp.channel, SUM(sp.spend) AS spend, SUM(sp.clicks) AS clicks
    FROM fact_spend sp
    JOIN dim_date dt ON dt.date_key = sp.date_key
    GROUP BY dt.month_start, sp.channel
),
conv_m AS (
    SELECT dt.month_start, s.channel,
           SUM(s.is_converting_session) AS conversions,
           COUNT(*) AS sessions
    FROM fact_sessions s
    JOIN dim_date dt ON dt.date_key = s.date_key
    GROUP BY dt.month_start, s.channel
)
SELECT
    COALESCE(c.month_start, sp.month_start) AS month_start,
    COALESCE(c.channel, sp.channel)         AS channel,
    COALESCE(sp.spend, 0)    AS spend,
    COALESCE(sp.clicks, 0)   AS clicks,
    COALESCE(c.sessions, 0)  AS sessions,
    COALESCE(c.conversions, 0) AS conversions,
    ROUND(COALESCE(sp.spend, 0) / NULLIF(c.conversions, 0), 2) AS cpa
FROM conv_m c
LEFT JOIN spend_m sp
       ON sp.month_start = c.month_start AND sp.channel = c.channel
ORDER BY month_start, channel;
