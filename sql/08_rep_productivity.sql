-- Rep productivity, quota attainment, and whether the team can carry the number.
--
-- The unfair comparison this table exists to prevent: ranking a rep hired
-- three months ago against one who has been in seat for two years. Quota is
-- pro-rated for ramp before attainment is computed, because a ramping rep was
-- never expected to carry a full number and reporting them at 40% is a
-- management error, not a performance finding.

DROP TABLE IF EXISTS rep_attainment;
CREATE TABLE rep_attainment AS
WITH tenure AS (
    SELECT
        r.rep_id,
        r.rep_name,
        r.segment,
        r.territory_id,
        r.annual_quota,
        r.ramp_months,
        r.hire_month,
        -- Months in seat across the reporting window.
        (SELECT COUNT(DISTINCT movement_month) FROM saas_fact_arr_movement) AS window_months,
        MAX(0, (CAST(SUBSTR(MAX(r.hire_month), 1, 4) AS INTEGER) * 12
                + CAST(SUBSTR(MAX(r.hire_month), 6, 2) AS INTEGER))) AS hire_index
    FROM saas_dim_rep r
    GROUP BY r.rep_id, r.rep_name, r.segment, r.territory_id,
             r.annual_quota, r.ramp_months, r.hire_month
),
bookings AS (
    SELECT rep_id,
           COUNT(*) AS won_deals,
           SUM(amount_arr) AS booked_arr
    FROM saas_fact_opportunity
    WHERE is_won = 1
    GROUP BY rep_id
),
activity AS (
    SELECT rep_id,
           COUNT(*) AS total_opportunities,
           SUM(CASE WHEN is_closed = 0 THEN 1 ELSE 0 END) AS open_opportunities,
           SUM(CASE WHEN is_closed = 0 THEN amount_arr ELSE 0 END) AS open_arr,
           ROUND(AVG(CASE WHEN is_closed = 1 THEN sales_cycle_days END), 1) AS avg_cycle_days
    FROM saas_fact_opportunity
    GROUP BY rep_id
)
SELECT
    t.rep_id,
    t.rep_name,
    t.segment,
    t.territory_id,
    t.hire_month,
    t.annual_quota,
    -- Quota earned over the window, net of the ramp period.
    ROUND(t.annual_quota / 12.0
          * MAX(0, (SELECT COUNT(DISTINCT movement_month) FROM saas_fact_arr_movement)
                   - t.ramp_months), 2) AS ramp_adjusted_quota,
    COALESCE(b.won_deals, 0) AS won_deals,
    ROUND(COALESCE(b.booked_arr, 0), 2) AS booked_arr,
    ROUND(100.0 * COALESCE(b.booked_arr, 0)
          / NULLIF(t.annual_quota / 12.0
                   * MAX(0, (SELECT COUNT(DISTINCT movement_month)
                             FROM saas_fact_arr_movement) - t.ramp_months), 0), 2)
        AS attainment_pct,
    COALESCE(a.total_opportunities, 0) AS total_opportunities,
    COALESCE(a.open_opportunities, 0)  AS open_opportunities,
    ROUND(COALESCE(a.open_arr, 0), 2)  AS open_pipeline_arr,
    a.avg_cycle_days
FROM tenure t
LEFT JOIN bookings b ON b.rep_id = t.rep_id
LEFT JOIN activity a ON a.rep_id = t.rep_id
ORDER BY attainment_pct DESC;


-- Territory view: is a weak number a rep problem or a territory problem?
DROP TABLE IF EXISTS territory_performance;
CREATE TABLE territory_performance AS
SELECT
    t.territory_id,
    t.territory_name,
    t.region,
    COUNT(DISTINCT r.rep_id) AS reps,
    COUNT(DISTINCT a.account_id) AS accounts,
    ROUND(SUM(CASE WHEN o.is_won = 1 THEN o.amount_arr ELSE 0 END), 2) AS booked_arr,
    ROUND(SUM(CASE WHEN o.is_closed = 0 THEN o.amount_arr ELSE 0 END), 2) AS open_arr,
    ROUND(100.0 * SUM(o.is_won) / NULLIF(SUM(o.is_closed), 0), 2) AS win_rate_pct,
    -- Accounts per rep is the capacity question: coverage thins as it climbs.
    ROUND(1.0 * COUNT(DISTINCT a.account_id) / NULLIF(COUNT(DISTINCT r.rep_id), 0), 1)
        AS accounts_per_rep
FROM saas_dim_territory t
LEFT JOIN saas_dim_rep r     ON r.territory_id = t.territory_id
LEFT JOIN saas_dim_account a ON a.territory_id = t.territory_id
LEFT JOIN saas_fact_opportunity o ON o.territory_id = t.territory_id
GROUP BY t.territory_id, t.territory_name, t.region
ORDER BY booked_arr DESC;


-- Capacity: how many reps the number needs, against how many are in seat.
-- Productivity is measured, not assumed — it is what the ramped reps actually
-- booked, which is the only defensible input to a hiring plan.
DROP TABLE IF EXISTS capacity_plan;
CREATE TABLE capacity_plan AS
WITH productivity AS (
    SELECT
        segment,
        COUNT(*) AS reps,
        SUM(booked_arr) AS booked_arr,
        AVG(booked_arr) AS arr_per_rep,
        AVG(attainment_pct) AS avg_attainment_pct
    FROM rep_attainment
    GROUP BY segment
),
target AS (
    SELECT segment, SUM(annual_quota) AS total_annual_quota
    FROM saas_dim_rep GROUP BY segment
)
SELECT
    p.segment,
    p.reps AS reps_in_seat,
    ROUND(p.arr_per_rep, 2) AS observed_arr_per_rep,
    ROUND(p.avg_attainment_pct, 2) AS avg_attainment_pct,
    ROUND(t.total_annual_quota, 2) AS total_annual_quota,
    -- Reps required if each new hire performs like the current average.
    CAST(CEIL(t.total_annual_quota / NULLIF(p.arr_per_rep, 0)) AS INTEGER)
        AS reps_required_at_observed_productivity,
    CAST(CEIL(t.total_annual_quota / NULLIF(p.arr_per_rep, 0)) - p.reps AS INTEGER)
        AS hiring_gap
FROM productivity p
JOIN target t ON t.segment = p.segment
ORDER BY hiring_gap DESC;
