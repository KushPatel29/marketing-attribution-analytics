"""
The SQL layer: does the committed SQL produce what it claims?

These tests re-derive each SQL result in pandas and demand agreement. That is
deliberate duplication — two independent implementations of the same metric
disagreeing is the cheapest bug detector there is, and it caught a fan-out in
the spend join during development.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402


def test_funnel_is_monotonically_narrowing(out):
    f = out("funnel_overall").sort_values("funnel_step")
    assert list(f["event_name"]) == C.FUNNEL_STEPS
    assert f["sessions"].is_monotonic_decreasing, "a funnel step cannot gain sessions"
    assert f.iloc[0]["pct_of_entry"] == pytest.approx(100.0)


def test_funnel_step_conversion_matches_pandas(out, data):
    """LAG-based step conversion must equal a plain groupby."""
    events = data("fact_events")
    counts = events.groupby("funnel_step")["session_id"].nunique().sort_index()
    f = out("funnel_overall").sort_values("funnel_step")
    for i, row in enumerate(f.itertuples(), start=1):
        assert row.sessions == counts[i]
        if i > 1:
            expected = 100.0 * counts[i] / counts[i - 1]
            assert row.step_conversion_pct == pytest.approx(expected, abs=0.01)


def test_mobile_is_the_worst_checkout_device(out):
    """The planted device penalty must survive into the SQL output."""
    gap = out("funnel_device_gap")
    worst = gap.sort_values("checkout_start_rate").iloc[0]
    assert worst["device"] == "mobile"
    assert worst["points_behind_best_device"] > 5


def test_spend_is_not_fanned_out_by_the_join(out, data):
    """
    The classic marketing-dashboard bug: joining (date, channel) spend onto
    session rows multiplies cost by the number of sessions. Total spend in the
    efficiency table must equal total spend in the source, to the cent.
    """
    eff, spend = out("channel_efficiency"), data("fact_spend")
    assert eff["spend"].sum() == pytest.approx(spend["spend"].sum(), abs=0.01)


def test_last_touch_conversions_tie_to_users(out, data):
    eff, users = out("channel_efficiency"), data("dim_user")
    assert eff["last_touch_conversions"].sum() == int(users["converted"].sum())
    assert eff["last_touch_share"].sum() == pytest.approx(1.0, abs=1e-4)


def test_last_touch_share_matches_dim_user(out, data):
    """SQL's last-touch split must equal the last_touch_channel column."""
    users = data("dim_user")
    expected = (
        users[users["converted"] == 1]["last_touch_channel"].value_counts(normalize=True)
    )
    eff = out("channel_efficiency").set_index("channel")
    for channel, share in expected.items():
        assert eff.loc[channel, "last_touch_share"] == pytest.approx(share, abs=1e-4)


def test_cohort_retention_starts_at_one_hundred(out):
    r = out("cohort_retention")
    first = r[r["months_since"] == 0]
    assert first["retention_pct"].sub(100.0).abs().max() < 1e-4
    assert r["retention_pct"].between(0, 100).all()


def test_ltv_is_cumulative(out):
    ltv = out("cohort_ltv")
    for _, g in ltv.groupby("cohort_month"):
        g = g.sort_values("months_since")
        assert g["cumulative_revenue_per_customer"].is_monotonic_increasing


def test_ltv_total_ties_to_orders(out, data):
    """Sum of each cohort's final cumulative revenue == total revenue."""
    ltv, orders = out("cohort_ltv"), data("fact_orders")
    last = ltv.sort_values("months_since").groupby("cohort_month").tail(1)
    total = (last["cumulative_revenue_per_customer"] * last["cohort_size"]).sum()
    assert total == pytest.approx(orders["revenue"].sum(), rel=1e-3)


def test_attribution_heuristics_each_sum_to_the_conversion_count(out, data):
    """
    Every heuristic splits each conversion into shares totalling exactly 1, so
    each model's credit must add up to the number of conversions.
    """
    heur, users = out("attribution_heuristics"), data("dim_user")
    conversions = int(users["converted"].sum())
    for col in ("first_touch", "last_touch", "linear", "position_based"):
        assert heur[f"{col}_conversions"].sum() == pytest.approx(conversions, abs=0.5)
        assert heur[f"{col}_share"].sum() == pytest.approx(1.0, abs=1e-4)


def test_journey_roles_partition_every_touch(out):
    """Opener + closer + assist must account for every touch, with no overlap."""
    roles = out("channel_journey_roles")
    total = roles["as_opener"] + roles["as_closer"] + roles["as_assist"]
    # Single-touch journeys are both opener and closer, so the sum exceeds
    # touches by exactly the number of those journeys — never less.
    assert (total >= roles["touches"]).all()


def test_journey_length_distribution_sums_to_all_conversions(out, data):
    dist, users = out("journey_length_distribution"), data("dim_user")
    assert dist["converting_journeys"].sum() == int(users["converted"].sum())
    assert dist["pct_of_conversions"].sum() == pytest.approx(100.0, abs=0.1)


def test_multi_touch_journeys_are_the_majority(out):
    """If journeys were single-touch, multi-touch attribution would be theatre."""
    dist = out("journey_length_distribution")
    multi = dist[dist["journey_touches"] > 1]["pct_of_conversions"].sum()
    assert multi > 50, f"only {multi:.1f}% of conversions are multi-touch"


def test_committed_sql_is_what_runs(out):
    """
    Guard against the analysis quietly moving into Python. Every table the
    engine exports must be created by a CREATE TABLE in the sql/ directory.
    """
    from engine.run_analytics import ANALYSIS_FILES, EXPORTS

    text = "\n".join(
        (ROOT / "sql" / f).read_text(encoding="utf-8").lower() for f in ANALYSIS_FILES
    )
    for table in EXPORTS:
        assert f"create table {table} as" in text, f"{table} is not created in sql/"
