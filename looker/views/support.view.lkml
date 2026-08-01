# The remaining joined views. Grouped into one file because none of them
# carries a metric definition worth arguing about — they exist so the explores
# above can slice by rep, account, territory, stage and product usage.

view: rep {
  sql_table_name: marts.saas_dim_rep ;;
  dimension: rep_id       { primary_key: yes type: number sql: ${TABLE}.rep_id ;; }
  dimension: rep_name     { type: string sql: ${TABLE}.rep_name ;; }
  dimension: segment      { type: string sql: ${TABLE}.segment ;; }
  dimension: hire_month   { type: string sql: ${TABLE}.hire_month ;; }
  dimension: ramp_months  { type: number sql: ${TABLE}.ramp_months ;; }
  dimension: annual_quota { type: number sql: ${TABLE}.annual_quota ;; }

  measure: reps        { type: count }
  measure: total_quota { type: sum sql: ${TABLE}.annual_quota ;; value_format_name: usd_0 }
}

view: account {
  sql_table_name: marts.saas_dim_account ;;
  dimension: account_id   { primary_key: yes type: number sql: ${TABLE}.account_id ;; }
  dimension: account_name { type: string sql: ${TABLE}.account_name ;; }
  dimension: segment      { type: string sql: ${TABLE}.segment ;; }
  dimension: industry     { type: string sql: ${TABLE}.industry ;; }
  dimension: employees    { type: number sql: ${TABLE}.employees ;; }

  measure: accounts { type: count }
}

view: territory {
  sql_table_name: marts.saas_dim_territory ;;
  dimension: territory_id   { primary_key: yes type: number sql: ${TABLE}.territory_id ;; }
  dimension: territory_name { type: string sql: ${TABLE}.territory_name ;; }
  dimension: region         { type: string sql: ${TABLE}.region ;; }
}

view: stage_history {
  sql_table_name: marts.saas_fact_stage_history ;;
  dimension: opportunity_id { type: number sql: ${TABLE}.opportunity_id ;; hidden: yes }
  dimension: stage_name     { type: string sql: ${TABLE}.stage_name ;; }
  dimension: stage_order    { type: number sql: ${TABLE}.stage_order ;; }
  dimension: days_in_stage  { type: number sql: ${TABLE}.days_in_stage ;; }

  measure: avg_days_in_stage {
    type: average
    sql: ${TABLE}.days_in_stage ;;
    value_format_name: decimal_1
  }
}

view: plg {
  sql_table_name: marts.saas_fact_plg ;;
  dimension: account_id     { primary_key: yes type: number sql: ${TABLE}.account_id ;; }
  dimension: segment        { type: string sql: ${TABLE}.segment ;; }
  dimension: source_channel { type: string sql: ${TABLE}.source_channel ;; }
  dimension: days_to_value  { type: number sql: ${TABLE}.days_to_value ;; }
  dimension: is_activated   { type: yesno  sql: ${TABLE}.is_activated = 1 ;; }
  dimension: is_pql         { type: yesno  sql: ${TABLE}.is_pql = 1 ;; }

  measure: signups   { type: count }
  measure: activated { type: count filters: [is_activated: "yes"] }
  measure: pqls      { type: count filters: [is_pql: "yes"] }

  measure: activation_rate {
    type: number
    sql: 1.0 * ${activated} / NULLIF(${signups}, 0) ;;
    value_format_name: percent_1
  }

  # Median, not mean: a handful of accounts that activate after three months
  # drag an average somewhere no product team can act on.
  measure: median_days_to_value {
    type: median
    sql: ${TABLE}.days_to_value ;;
    value_format_name: decimal_1
  }
}
