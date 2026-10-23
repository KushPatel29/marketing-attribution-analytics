-- Weekly cohorts of new users. A user is new when the export holds their
-- first_visit event; their cohort is that week (Monday start). Users first
-- seen without a first_visit had visited before the export began, so they
-- are left out rather than passed off as new. Week k is the share of the
-- cohort with a session k weeks later. Only whole weeks with four full weeks
-- of follow-up inside the export are reported (2 Nov to 28 Dec 2020).
WITH first_visit AS (
  SELECT user_pseudo_id, DATE_TRUNC(MIN(PARSE_DATE('%Y%m%d', event_date)), WEEK(MONDAY)) AS cohort_week
  FROM `bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*`
  WHERE event_name = 'first_visit'
  GROUP BY 1
),
activity AS (
  SELECT DISTINCT
    f.user_pseudo_id, f.cohort_week,
    DATE_DIFF(DATE_TRUNC(s.session_date, WEEK(MONDAY)), f.cohort_week, WEEK) AS week_n
  FROM ga4_portfolio.sessions s JOIN first_visit f USING (user_pseudo_id)
),
cohorts AS (
  SELECT
    f.cohort_week,
    COUNT(DISTINCT f.user_pseudo_id) AS users,
    COUNT(DISTINCT IF(a.week_n = 1, a.user_pseudo_id, NULL)) AS w1,
    COUNT(DISTINCT IF(a.week_n = 2, a.user_pseudo_id, NULL)) AS w2,
    COUNT(DISTINCT IF(a.week_n = 3, a.user_pseudo_id, NULL)) AS w3,
    COUNT(DISTINCT IF(a.week_n = 4, a.user_pseudo_id, NULL)) AS w4
  FROM first_visit f LEFT JOIN activity a USING (user_pseudo_id, cohort_week)
  WHERE f.cohort_week >= DATE '2020-11-02'
    AND DATE_ADD(f.cohort_week, INTERVAL 35 DAY) <= DATE '2021-02-01'
  GROUP BY 1
)
SELECT TO_JSON_STRING(STRUCT(
  (SELECT COUNT(*) FROM first_visit) AS new_users_in_export,
  (SELECT ARRAY_AGG(STRUCT(cohort_week, users, w1, w2, w3, w4,
                           ROUND(w1 / users, 4) AS r1, ROUND(w4 / users, 4) AS r4) ORDER BY cohort_week)
     FROM cohorts) AS cohorts,
  (SELECT AS STRUCT SUM(users) AS users, ROUND(SUM(w1) / SUM(users), 4) AS week1,
          ROUND(SUM(w4) / SUM(users), 4) AS week4 FROM cohorts) AS pooled
)) AS retention;
