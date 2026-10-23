"""
Act three runs in BigQuery against Google's public GA4 export, not in CI.
What BigQuery returned is committed (ga4_bigquery/results.json, with job IDs);
these tests hold those numbers to each other, to the SQL that produced them and
to the README, so a number cannot be edited in one place and not the others.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GA4 = ROOT / "ga4_bigquery"
RESULTS = json.loads((GA4 / "results.json").read_text(encoding="utf-8"))
README = (ROOT / "README.md").read_text(encoding="utf-8")


def v(name):
    return RESULTS[name]["value"]


def test_every_result_names_its_sql_and_its_job():
    for name in ("built", "quality", "funnel", "attribution", "retention", "reconcile"):
        r = RESULTS[name]
        assert (ROOT / r["sql"]).exists(), r["sql"]
        assert re.fullmatch(r"eai6010m05kushp:US\.job_[\w-]+", r["job_id"]), r["job_id"]
    ids = [
        RESULTS[n]["job_id"]
        for n in ("built", "quality", "funnel", "attribution", "retention", "reconcile")
    ]
    assert len(set(ids)) == len(ids)


def test_the_session_table_and_the_raw_events_agree_exactly():
    rec = v("reconcile")
    shared = set(rec["from_sessions"])
    assert shared <= set(rec["from_events"])
    for k in shared:
        assert rec["from_events"][k] == rec["from_sessions"][k], k
    assert v("built")["sessions"] == v("quality")["sessions"] == rec["from_events"]["sessions"]
    assert v("built")["transaction_revenue"] == rec["from_events"]["transaction_revenue"]


def test_every_purchase_event_is_accounted_for():
    q = v("quality")
    assert q["purchase_events"] == (
        q["purchase_id_null"]
        + q["purchase_id_not_set"]
        + q["transactions"]
        + q["duplicate_purchase_events"]
    )
    assert q["transactions_sent_more_than_once"] <= q["duplicate_purchase_events"]
    assert v("reconcile")["from_events"]["transactions_with_null_revenue"] == 0, (
        "the null-revenue purchase events are all duplicates or ID-less"
    )
    assert q["revenue_summed_over_every_purchase_event"] > v("built")["transaction_revenue"]


def test_the_funnels_are_internally_possible():
    f = v("funnel")
    open_, strict, tagged = f["open_funnel"], f["strict_funnel"], f["as_tagged_funnel"]
    steps = [
        "view_item",
        "add_to_cart",
        "begin_checkout",
        "add_shipping_info",
        "add_payment_info",
        "purchase",
    ]
    for step in steps:
        assert strict[step] <= open_[step], step
    assert [strict[s] for s in steps] == sorted((strict[s] for s in steps), reverse=True)
    tagged_steps = ["view_item", "add_to_cart", "checkout", "add_payment_info", "purchase"]
    assert [tagged[s] for s in tagged_steps] == sorted(
        (tagged[s] for s in tagged_steps), reverse=True
    )
    assert (
        tagged["purchase"] >= strict["purchase"] and tagged["checkout"] >= strict["begin_checkout"]
    )
    order = f["tagging_order"]
    assert (
        order["shipping_logged_before_begin_checkout"] / order["sessions_with_both_checkout_events"]
        > 0.5
    )
    assert sum(d["sessions"] for d in f["open_by_device"]) == open_["sessions"]
    assert sum(d["purchase"] for d in f["open_by_device"]) == open_["purchase"]


def test_each_attribution_rule_hands_out_all_the_revenue_once():
    a = v("attribution")
    rules = {r["rule"] for r in a["by_rule"]}
    assert rules == {"first_touch", "last_click", "last_non_direct_click", "last_real_touch"}
    for rule in rules:
        rows = [r for r in a["by_rule"] if r["rule"] == rule]
        assert sum(r["revenue"] for r in rows) == pytest.approx(a["total"]["revenue"], abs=0.01), (
            rule
        )
        assert sum(r["transactions"] for r in rows) == a["total"]["transactions"], rule
        assert sum(r["share"] for r in rows) == pytest.approx(1, abs=0.001), rule
    assert a["total"] == {
        "revenue": v("built")["transaction_revenue"],
        "transactions": v("built")["transactions"],
    }


def test_the_share_that_moves_is_the_total_variation_distance():
    a = v("attribution")

    def shares(rule):
        return {
            r["channel"]: r["revenue"] / a["total"]["revenue"]
            for r in a["by_rule"]
            if r["rule"] == rule
        }

    def tvd(x, y):
        x, y = shares(x), shares(y)
        return sum(abs(x.get(c, 0) - y.get(c, 0)) for c in set(x) | set(y)) / 2

    moves = a["revenue_share_that_moves"]
    assert tvd("first_touch", "last_click") == pytest.approx(
        moves["first_touch_vs_last_click"], abs=1e-4
    )
    assert tvd("last_non_direct_click", "last_real_touch") == pytest.approx(
        moves["last_non_direct_vs_last_real_touch"], abs=1e-4
    )


def test_self_referrals_are_inside_last_click_referral():
    a = v("attribution")
    referral = next(
        r for r in a["by_rule"] if r["rule"] == "last_click" and r["channel"] == "Referral"
    )
    assert a["self_referral_last_click"]["revenue"] <= referral["revenue"]
    assert a["self_referral_last_click"]["transactions"] <= referral["transactions"]
    rt = {r["channel"]: r for r in a["by_rule"] if r["rule"] == "last_real_touch"}
    lnd = {r["channel"]: r for r in a["by_rule"] if r["rule"] == "last_non_direct_click"}
    assert rt["Referral"]["revenue"] < lnd["Referral"]["revenue"]
    assert rt["Organic Search"]["revenue"] > lnd["Organic Search"]["revenue"]


def test_the_pooled_retention_is_the_sum_of_its_cohorts():
    r = v("retention")
    users = sum(c["users"] for c in r["cohorts"])
    assert r["pooled"]["users"] == users
    assert r["pooled"]["week1"] == pytest.approx(
        sum(c["w1"] for c in r["cohorts"]) / users, abs=5e-5
    )
    assert r["pooled"]["week4"] == pytest.approx(
        sum(c["w4"] for c in r["cohorts"]) / users, abs=5e-5
    )
    for c in r["cohorts"]:
        assert c["r1"] == pytest.approx(c["w1"] / c["users"], abs=5e-5)
    assert [c["cohort_week"] for c in r["cohorts"]][0] == "2020-11-02"
    assert r["pooled"]["users"] <= r["new_users_in_export"]


def test_the_sql_does_what_the_readme_says():
    sessions = (GA4 / "01_sessions.sql").read_text(encoding="utf-8")
    assert "PARTITION BY transaction_id ORDER BY event_timestamp" in sessions
    for path in GA4.glob("*.sql"):
        dml = re.search(
            r"^\s*(INSERT|UPDATE|DELETE|MERGE)\b", path.read_text(encoding="utf-8"), re.M | re.I
        )
        assert not dml, f"{path.name}: the sandbox refuses DML"
    funnel = (GA4 / "03_funnel.sql").read_text(encoding="utf-8")
    assert "as_tagged_funnel" in funnel and "strict_funnel" in funnel
    assert "first_visit" in (GA4 / "05_retention.sql").read_text(encoding="utf-8")


def _money(x):
    return f"${x:,.0f}"


def test_the_readme_quotes_the_recorded_numbers():
    q, a, f, r = v("quality"), v("attribution"), v("funnel"), v("retention")
    built = v("built")
    for phrase in (
        f"{q['events']:,} events",
        f"{built['sessions']:,} sessions",
        f"{q['duplicate_purchase_events']} duplicate purchase events",
        f"{q['purchase_id_null'] + q['purchase_id_not_set']:,} purchase events with no "
        "transaction ID",
        f"{built['transactions']:,} transactions",
        _money(built["transaction_revenue"]),
        _money(q["revenue_summed_over_every_purchase_event"]),
        f"{f['tagging_order']['shipping_logged_before_begin_checkout']:,} of "
        f"{f['tagging_order']['sessions_with_both_checkout_events']:,}",
        _money(a["self_referral_last_click"]["revenue"]),
        f"{a['revenue_share_that_moves']['first_touch_vs_last_click']:.1%}",
        f"{a['revenue_share_that_moves']['last_non_direct_vs_last_real_touch']:.1%}",
        f"{r['pooled']['week1']:.1%}",
    ):
        assert phrase in README, phrase
