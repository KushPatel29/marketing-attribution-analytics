# NRR and GRR live here so they cannot be re-derived slightly differently in
# every dashboard that needs them. Both are exposed on purpose: a business can
# post healthy net retention while losing a fifth of its customers, and only
# the gross number shows that.

view: arr_movement {
  sql_table_name: marts.saas_fact_arr_movement ;;

  dimension: subscription_id { type: string sql: ${TABLE}.subscription_id ;; }
  dimension: account_id      { type: number sql: ${TABLE}.account_id ;; hidden: yes }
  dimension: segment         { type: string sql: ${TABLE}.segment ;; }
  dimension: movement_type   { type: string sql: ${TABLE}.movement_type ;; }
  dimension: arr_delta       { type: number sql: ${TABLE}.arr_delta ;; }

  dimension_group: movement {
    type: time
    timeframes: [month, quarter, year]
    sql: ${TABLE}.movement_month ;;
  }

  measure: new_arr {
    type: sum
    sql: ${TABLE}.arr_delta ;;
    filters: [movement_type: "new"]
    value_format_name: usd_0
  }

  measure: expansion_arr {
    type: sum
    sql: ${TABLE}.arr_delta ;;
    filters: [movement_type: "expansion"]
    value_format_name: usd_0
  }

  measure: contraction_arr {
    type: sum
    sql: ${TABLE}.arr_delta ;;
    filters: [movement_type: "contraction"]
    value_format_name: usd_0
  }

  measure: churned_arr {
    type: sum
    sql: ${TABLE}.arr_delta ;;
    filters: [movement_type: "churn"]
    value_format_name: usd_0
  }

  measure: net_new_arr {
    type: sum
    sql: ${TABLE}.arr_delta ;;
    value_format_name: usd_0
  }

  # Starting ARR of the subscriptions that moved: arr_after minus the delta.
  measure: starting_arr {
    type: sum
    sql: ${TABLE}.arr_after - ${TABLE}.arr_delta ;;
    filters: [movement_type: "expansion,contraction,churn"]
    value_format_name: usd_0
  }

  measure: net_revenue_retention {
    type: number
    sql: (${starting_arr} + ${expansion_arr} + ${contraction_arr} + ${churned_arr})
         / NULLIF(${starting_arr}, 0) ;;
    value_format_name: percent_1
  }

  measure: gross_revenue_retention {
    type: number
    sql: (${starting_arr} + ${contraction_arr} + ${churned_arr})
         / NULLIF(${starting_arr}, 0) ;;
    value_format_name: percent_1
  }

  measure: logos_churned {
    type: count_distinct
    sql: ${TABLE}.subscription_id ;;
    filters: [movement_type: "churn"]
  }
}
