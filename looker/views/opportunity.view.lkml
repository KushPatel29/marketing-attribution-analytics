# The one place "Win Rate", "ACV" and "Sales Cycle" are defined.
#
# Note that win rate is a `number` measure built from two other measures, not a
# raw AVG(is_won). Averaging the flag silently puts open deals in the
# denominator and understates the number all quarter — the most common wrong
# metric on a sales dashboard, and the reason a semantic layer is worth having.

view: opportunity {
  sql_table_name: marts.saas_fact_opportunity ;;

  dimension: opportunity_id {
    primary_key: yes
    type: number
    sql: ${TABLE}.opportunity_id ;;
  }

  dimension: account_id       { type: number sql: ${TABLE}.account_id ;;    hidden: yes }
  dimension: rep_id           { type: number sql: ${TABLE}.rep_id ;;        hidden: yes }
  dimension: territory_id     { type: number sql: ${TABLE}.territory_id ;;  hidden: yes }
  dimension: segment          { type: string sql: ${TABLE}.segment ;; }
  dimension: source_channel   { type: string sql: ${TABLE}.source_channel ;; }
  dimension: stage_name       { type: string sql: ${TABLE}.stage_name ;; }
  dimension: loss_reason      { type: string sql: ${TABLE}.loss_reason ;; }
  dimension: competitor       { type: string sql: ${TABLE}.competitor ;; }
  dimension: amount_arr       { type: number sql: ${TABLE}.amount_arr ;; }
  dimension: sales_cycle_days { type: number sql: ${TABLE}.sales_cycle_days ;; }
  dimension: is_closed        { type: yesno  sql: ${TABLE}.is_closed = 1 ;; }
  dimension: is_won           { type: yesno  sql: ${TABLE}.is_won = 1 ;; }

  dimension_group: created {
    type: time
    timeframes: [date, week, month, quarter, year]
    sql: ${TABLE}.created_date ;;
  }

  dimension_group: close {
    type: time
    timeframes: [date, week, month, quarter, year]
    sql: ${TABLE}.close_date ;;
  }

  measure: opportunities { type: count }

  measure: closed_opportunities {
    type: count
    filters: [is_closed: "yes"]
  }

  measure: won_opportunities {
    type: count
    filters: [is_won: "yes"]
  }

  # Denominator is CLOSED deals, not all deals.
  measure: win_rate {
    type: number
    sql: 1.0 * ${won_opportunities} / NULLIF(${closed_opportunities}, 0) ;;
    value_format_name: percent_1
  }

  measure: new_arr {
    type: sum
    sql: ${TABLE}.amount_arr ;;
    filters: [is_won: "yes"]
    value_format_name: usd_0
  }

  measure: open_pipeline_arr {
    type: sum
    sql: ${TABLE}.amount_arr ;;
    filters: [is_closed: "no"]
    value_format_name: usd_0
  }

  measure: average_deal_size {
    type: average
    sql: ${TABLE}.amount_arr ;;
    filters: [is_won: "yes"]
    value_format_name: usd_0
  }

  # Won deals only. Mixing in losses answers "how long until we give up",
  # which is a different question with a different owner.
  measure: avg_sales_cycle_days {
    type: average
    sql: ${TABLE}.sales_cycle_days ;;
    filters: [is_won: "yes"]
    value_format_name: decimal_1
  }
}
