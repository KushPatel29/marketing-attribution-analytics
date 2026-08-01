-- The three heuristic attribution models that need no library: first-touch,
-- last-touch, and linear. All three are one pass over the touch table with
-- window functions, which is worth knowing — a team can have these in the
-- warehouse this afternoon without a data scientist.
--
-- Every model here splits each conversion into shares that sum to exactly 1.
-- `attribution/evaluate.py` checks that invariant and then grades the result
-- against the planted truth, where it does NOT hold. That mismatch is the
-- point of the project, not a bug in these queries.

-- Touches belonging to journeys that converted. Non-converting journeys have
-- nothing to attribute, so they are filtered here once.
DROP TABLE IF EXISTS converting_touches;
CREATE TABLE converting_touches AS
SELECT
    s.user_id,
    s.session_id,
    s.channel,
    s.touch_position,
    COUNT(*)  OVER (PARTITION BY s.user_id) AS journey_touches,
    MIN(s.touch_position) OVER (PARTITION BY s.user_id) AS first_position,
    MAX(s.touch_position) OVER (PARTITION BY s.user_id) AS last_position
FROM fact_sessions s
JOIN dim_user u ON u.user_id = s.user_id
WHERE u.converted = 1;


DROP TABLE IF EXISTS attribution_heuristics;
CREATE TABLE attribution_heuristics AS
WITH credited AS (
    SELECT
        channel,
        -- First touch: all credit to the opener.
        CASE WHEN touch_position = first_position THEN 1.0 ELSE 0.0 END AS first_touch,
        -- Last touch: all credit to the closer. The industry default.
        CASE WHEN touch_position = last_position  THEN 1.0 ELSE 0.0 END AS last_touch,
        -- Linear: split evenly across every touch in the journey.
        1.0 / journey_touches AS linear,
        -- Position-based (40/20/40): opener and closer take 40% each, the
        -- middle shares 20%.
        --
        -- The two-touch case needs its own branch. With no middle touch the
        -- 20% has nowhere to go, and 40/40 silently loses a fifth of every
        -- such conversion — the credit stops summing to 1 and every share
        -- downstream is quietly wrong. A two-touch journey splits 50/50.
        CASE
            WHEN journey_touches = 1 THEN 1.0
            WHEN journey_touches = 2 THEN 0.5
            WHEN touch_position = first_position THEN 0.4
            WHEN touch_position = last_position  THEN 0.4
            ELSE 0.2 / (journey_touches - 2)
        END AS position_based
    FROM converting_touches
)
SELECT
    channel,
    ROUND(SUM(first_touch), 2)    AS first_touch_conversions,
    ROUND(SUM(last_touch), 2)     AS last_touch_conversions,
    ROUND(SUM(linear), 2)         AS linear_conversions,
    ROUND(SUM(position_based), 2) AS position_based_conversions,
    ROUND(SUM(first_touch)    / SUM(SUM(first_touch))    OVER (), 6) AS first_touch_share,
    ROUND(SUM(last_touch)     / SUM(SUM(last_touch))     OVER (), 6) AS last_touch_share,
    ROUND(SUM(linear)         / SUM(SUM(linear))         OVER (), 6) AS linear_share,
    ROUND(SUM(position_based) / SUM(SUM(position_based)) OVER (), 6) AS position_based_share
FROM credited
GROUP BY channel
ORDER BY last_touch_share DESC;


-- How often does each channel appear in each journey role? This is the table
-- that explains *why* last-touch is wrong here: a channel can dominate the
-- closing position while opening almost nothing.
DROP TABLE IF EXISTS channel_journey_roles;
CREATE TABLE channel_journey_roles AS
SELECT
    channel,
    COUNT(*) AS touches,
    SUM(CASE WHEN touch_position = first_position THEN 1 ELSE 0 END) AS as_opener,
    SUM(CASE WHEN touch_position = last_position  THEN 1 ELSE 0 END) AS as_closer,
    SUM(CASE WHEN touch_position <> first_position
              AND touch_position <> last_position THEN 1 ELSE 0 END) AS as_assist,
    ROUND(100.0 * SUM(CASE WHEN touch_position = last_position THEN 1 ELSE 0 END)
          / COUNT(*), 2) AS pct_of_its_touches_closing
FROM converting_touches
GROUP BY channel
ORDER BY as_closer DESC;


-- Journey shape. Multi-touch attribution only matters if journeys are actually
-- multi-touch; this table is the evidence that they are.
DROP TABLE IF EXISTS journey_length_distribution;
CREATE TABLE journey_length_distribution AS
WITH per_user AS (
    SELECT user_id, journey_touches, COUNT(DISTINCT channel) AS distinct_channels
    FROM converting_touches
    GROUP BY user_id, journey_touches
)
SELECT
    journey_touches,
    COUNT(*) AS converting_journeys,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_conversions,
    ROUND(AVG(distinct_channels), 2) AS avg_distinct_channels
FROM per_user
GROUP BY journey_touches
ORDER BY journey_touches;
