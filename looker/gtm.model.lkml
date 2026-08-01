# LookML semantic layer over the GTM marts.
#
# Why this exists: every metric in this repo is currently defined once, in SQL,
# and consumed by scripts that agree with each other because they read the same
# table. The moment a BI tool is added, that stops being true — someone builds
# "Win Rate" in a dashboard, someone else builds it in a spreadsheet, and the
# two disagree by a filter nobody wrote down. A semantic layer is where the
# definition goes so it can only be written once.
#
# Committed as configuration rather than as a screenshot of a Looker instance:
# a parser test in tests/test_saas_gtm.py asserts every field referenced here
# exists in the marts the SQL actually produces, so this cannot drift into
# describing a warehouse that no longer looks like this.

connection: "gtm_warehouse"

include: "/views/*.view.lkml"

explore: opportunity {
  label: "Pipeline"
  description: "Opportunities, their stage history, the rep who owns them, and the account."

  join: stage_history {
    type: left_outer
    relationship: one_to_many
    sql_on: ${opportunity.opportunity_id} = ${stage_history.opportunity_id} ;;
  }

  join: rep {
    type: left_outer
    relationship: many_to_one
    sql_on: ${opportunity.rep_id} = ${rep.rep_id} ;;
  }

  join: account {
    type: left_outer
    relationship: many_to_one
    sql_on: ${opportunity.account_id} = ${account.account_id} ;;
  }

  join: territory {
    type: left_outer
    relationship: many_to_one
    sql_on: ${opportunity.territory_id} = ${territory.territory_id} ;;
  }
}

explore: arr_movement {
  label: "ARR Waterfall"
  description: "New, expansion, contraction and churn. Every retention metric is an aggregation of this."
}

explore: plg {
  label: "Product-Led Funnel"
  description: "Self-serve signup, activation, and product-qualified accounts."
}
