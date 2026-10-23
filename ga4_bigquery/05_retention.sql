-- Weekly cohorts: a user's cohort is the week (Monday start) of their first
-- session in the export; week k is the share of the cohort with a session
-- k weeks later. Only cohorts with four full weeks of follow-up inside the
-- export are reported, so no cohort looks worse for having been cut short.
WITH first_seen AS (
  SELECT user_pseudo_id, DATE_TRUNC(MIN(session_date), WEEK(MONDAY)) AS cohort_week
  FROM ga4_portfolio.sessions GROUP BY 1
),
activity AS (
  SELECT DISTINCT
    f.user_pseudo_id, f.cohort_week,
    DATE_DIFF(DATE_TRUNC(s.session_date, WEEK(MONDAY)), f.cohort_week, WEEK) AS week_n
  FROM ga4_portfolio.sessions s JOIN first_seen f USING (user_pseudo_id)
),
cohorts AS (
  SELECT
    cohort_week,
    COUNT(DISTINCT user_pseudo_id) AS users,
    COUNT(DISTINCT IF(week_n = 1, user_pseudo_id, NULL)) AS w1,
    COUNT(DISTINCT IF(week_n = 2, user_pseudo_id, NULL)) AS w2,
    COUNT(DISTINCT IF(week_n = 3, user_pseudo_id, NULL)) AS w3,
    COUNT(DISTINCT IF(week_n = 4, user_pseudo_id, NULL)) AS w4
  FROM activity
  GROUP BY 1
)
SELECT TO_JSON_STRING(STRUCT(
  (SELECT ARRAY_AGG(STRUCT(cohort_week, users, w1, w2, w3, w4,
                           ROUND(w1 / users, 4) AS r1, ROUND(w4 / users, 4) AS r4) ORDER BY cohort_week)
     FROM cohorts
     WHERE DATE_ADD(cohort_week, INTERVAL 35 DAY) <= DATE '2021-02-01') AS cohorts,
  (SELECT AS STRUCT SUM(users) AS users, ROUND(SUM(w1) / SUM(users), 4) AS week1, ROUND(SUM(w4) / SUM(users), 4) AS week4
     FROM cohorts WHERE DATE_ADD(cohort_week, INTERVAL 35 DAY) <= DATE '2021-02-01') AS pooled
)) AS retention;
