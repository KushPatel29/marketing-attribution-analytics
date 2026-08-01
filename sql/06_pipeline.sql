-- Pipeline: the CRM funnel, its conversion, its speed, and why deals die.
--
-- Everything here is the B2B analogue of sql/02_funnel.sql. The difference is
-- that a session either converts or doesn't, whereas an opportunity *sits* in
-- a stage for weeks — so stage conversion has to be computed from how far each
-- deal actually walked, and speed needs its own table.

-- How far each opportunity got. A deal that dies at Demo reached Prospecting,
-- Discovery and Demo — the funnel counts it at all three, which is what makes
-- stage-to-stage conversion meaningful rather than a snapshot of where deals
-- happen to be sitting today.
DROP TABLE IF EXISTS opportunity_reach;
CREATE TABLE opportunity_reach AS
SELECT
    o.opportunity_id,
    o.segment,
    o.source_channel,
    o.is_closed,
    o.is_won,
    o.amount_arr,
    MAX(h.stage_order) AS deepest_stage_order
FROM saas_fact_opportunity o
JOIN saas_fact_stage_history h ON h.opportunity_id = o.opportunity_id
WHERE h.stage_name NOT IN ('Closed Won', 'Closed Lost')
GROUP BY o.opportunity_id, o.segment, o.source_channel,
         o.is_closed, o.is_won, o.amount_arr;


DROP TABLE IF EXISTS pipeline_funnel;
CREATE TABLE pipeline_funnel AS
WITH open_stages AS (
    SELECT stage_name, stage_order FROM saas_dim_stage WHERE is_closed = 0
),
reached AS (
    SELECT
        s.stage_order,
        s.stage_name,
        COUNT(*) AS opportunities,
        ROUND(SUM(r.amount_arr), 2) AS arr_reaching_stage
    FROM open_stages s
    JOIN opportunity_reach r ON r.deepest_stage_order >= s.stage_order
    GROUP BY s.stage_order, s.stage_name
)
SELECT
    stage_order,
    stage_name,
    opportunities,
    arr_reaching_stage,
    LAG(opportunities) OVER (ORDER BY stage_order) AS prev_stage_opportunities,
    ROUND(100.0 * opportunities
          / LAG(opportunities) OVER (ORDER BY stage_order), 2) AS stage_conversion_pct,
    ROUND(100.0 * opportunities
          / FIRST_VALUE(opportunities) OVER (ORDER BY stage_order), 2) AS pct_of_created
FROM reached
ORDER BY stage_order;


-- Velocity: how long a deal sits in each stage. The slowest stage is where a
-- sales process is actually broken, and it is almost never the last one.
DROP TABLE IF EXISTS stage_velocity;
CREATE TABLE stage_velocity AS
SELECT
    h.stage_name,
    h.stage_order,
    o.segment,
    COUNT(*) AS times_entered,
    ROUND(AVG(h.days_in_stage), 1) AS avg_days_in_stage,
    MAX(h.days_in_stage) AS max_days_in_stage
FROM saas_fact_stage_history h
JOIN saas_fact_opportunity o ON o.opportunity_id = h.opportunity_id
WHERE h.stage_name NOT IN ('Closed Won', 'Closed Lost')
GROUP BY h.stage_name, h.stage_order, o.segment
ORDER BY o.segment, h.stage_order;


-- Win rate and sales-cycle length by segment. Enterprise wins less and takes
-- longer; reporting one blended number hides both facts and makes the SMB
-- team look bad for a cycle they do not run.
DROP TABLE IF EXISTS win_rate_by_segment;
CREATE TABLE win_rate_by_segment AS
SELECT
    segment,
    COUNT(*) AS closed_opportunities,
    SUM(is_won) AS won,
    ROUND(100.0 * SUM(is_won) / COUNT(*), 2) AS win_rate_pct,
    ROUND(SUM(CASE WHEN is_won = 1 THEN amount_arr ELSE 0 END), 2) AS won_arr,
    ROUND(100.0 * SUM(CASE WHEN is_won = 1 THEN amount_arr ELSE 0 END)
          / SUM(amount_arr), 2) AS arr_win_rate_pct,
    ROUND(AVG(sales_cycle_days), 1) AS avg_sales_cycle_days,
    ROUND(AVG(CASE WHEN is_won = 1 THEN sales_cycle_days END), 1) AS avg_cycle_won,
    ROUND(AVG(CASE WHEN is_won = 0 THEN sales_cycle_days END), 1) AS avg_cycle_lost
FROM saas_fact_opportunity
WHERE is_closed = 1
GROUP BY segment
ORDER BY won_arr DESC;


-- Loss reasons, weighted by dollars rather than by count. Losing ten SMB deals
-- on price and one Enterprise deal to a competitor are not the same problem.
DROP TABLE IF EXISTS loss_analysis;
CREATE TABLE loss_analysis AS
SELECT
    loss_reason,
    competitor,
    COUNT(*) AS losses,
    ROUND(SUM(amount_arr), 2) AS arr_lost,
    ROUND(100.0 * SUM(amount_arr) / SUM(SUM(amount_arr)) OVER (), 2) AS pct_of_lost_arr
FROM saas_fact_opportunity
WHERE is_closed = 1 AND is_won = 0
GROUP BY loss_reason, competitor
ORDER BY arr_lost DESC;


-- Pipeline coverage: open pipeline against the quota it has to cover.
--
-- Everyone quotes "3x". Three is a blended heuristic borrowed from a
-- transactional motion, and applying it to every segment is wrong in both
-- directions at once. The coverage a segment actually needs falls out of two
-- numbers it already has:
--
--     required coverage = (1 / win rate) x (sales cycle / days in a quarter)
--
-- The first term says how much pipeline a win consumes. The second says how
-- many quarters of pipeline must already be in flight, because a deal that
-- takes 148 days to close cannot be sourced inside the quarter it lands in.
-- Enterprise here wins ~16% on a ~148-day cycle and therefore needs roughly
-- 10x, not 3x -- so the blended rule would have called a dangerously thin
-- Enterprise pipeline "covered" while nagging a perfectly healthy SMB team.
DROP TABLE IF EXISTS pipeline_coverage;
CREATE TABLE pipeline_coverage AS
WITH open_pipe AS (
    SELECT segment, SUM(amount_arr) AS open_arr, COUNT(*) AS open_opps
    FROM saas_fact_opportunity
    WHERE is_closed = 0
    GROUP BY segment
),
quota AS (
    -- One quarter of annual quota, since coverage is a quarterly conversation.
    SELECT segment, SUM(annual_quota) / 4.0 AS quarterly_quota, COUNT(*) AS reps
    FROM saas_dim_rep
    GROUP BY segment
),
rates AS (
    SELECT
        segment,
        1.0 * SUM(is_won) / COUNT(*) AS win_rate,
        AVG(sales_cycle_days) AS avg_cycle_days
    FROM saas_fact_opportunity WHERE is_closed = 1 GROUP BY segment
)
SELECT
    q.segment,
    q.reps,
    ROUND(q.quarterly_quota, 2) AS quarterly_quota,
    COALESCE(p.open_opps, 0) AS open_opportunities,
    ROUND(COALESCE(p.open_arr, 0), 2) AS open_pipeline_arr,
    ROUND(COALESCE(p.open_arr, 0) / NULLIF(q.quarterly_quota, 0), 2) AS coverage_ratio,
    ROUND(100.0 * r.win_rate, 2) AS win_rate_pct,
    ROUND(r.avg_cycle_days, 1) AS avg_cycle_days,
    -- Pipeline discounted by the segment's own historical win rate.
    ROUND(COALESCE(p.open_arr, 0) * r.win_rate, 2) AS weighted_pipeline_arr,
    ROUND((1.0 / NULLIF(r.win_rate, 0)) * (r.avg_cycle_days / 91.0), 2)
        AS required_coverage,
    CASE WHEN COALESCE(p.open_arr, 0) / NULLIF(q.quarterly_quota, 0)
              >= (1.0 / NULLIF(r.win_rate, 0)) * (r.avg_cycle_days / 91.0)
         THEN 'covered' ELSE 'short' END AS coverage_verdict,
    -- What the industry rule of thumb would have said, kept alongside so the
    -- two verdicts can be compared rather than one quietly replacing the other.
    CASE WHEN COALESCE(p.open_arr, 0) / NULLIF(q.quarterly_quota, 0) >= 3.0
         THEN 'covered' ELSE 'short' END AS verdict_under_3x_rule
FROM quota q
LEFT JOIN open_pipe p ON p.segment = q.segment
LEFT JOIN rates r     ON r.segment = q.segment
ORDER BY coverage_ratio;
