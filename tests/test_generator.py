"""
The generator's own invariants, and the integrity of the planted truth.

If these fail, every number downstream is meaningless — so they run first and
they are deliberately strict.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from data_generator.generate_marketing_data import conversion_probability  # noqa: E402


def test_row_counts_and_keys(data):
    users, sessions, journeys = data("dim_user"), data("fact_sessions"), data("fact_journeys")
    assert len(users) == C.N_USERS
    assert len(journeys) == C.N_USERS
    assert users["user_id"].is_unique
    assert sessions["session_id"].is_unique
    # Every session belongs to a real user.
    assert set(sessions["user_id"]) <= set(users["user_id"])


def test_journey_path_matches_sessions(data):
    """The path string the attribution models read must equal the session facts."""
    journeys, sessions = data("fact_journeys"), data("fact_sessions")
    rebuilt = (
        sessions.sort_values(["user_id", "touch_position"])
        .groupby("user_id")["channel"]
        .apply(lambda s: ">".join(s))
    )
    merged = journeys.set_index("user_id").join(rebuilt.rename("rebuilt"))
    assert (merged["path"] == merged["rebuilt"]).all()


def test_first_and_last_touch_agree_with_path(data):
    users, journeys = data("dim_user"), data("fact_journeys")
    m = users.merge(journeys, on="user_id")
    assert (m["first_touch_channel"] == m["path"].str.split(">").str[0]).all()
    assert (m["last_touch_channel"] == m["path"].str.split(">").str[-1]).all()


def test_only_converting_journeys_have_a_converting_session(data):
    users, sessions = data("dim_user"), data("fact_sessions")
    conv_users = set(users.loc[users["converted"] == 1, "user_id"])
    flagged = set(sessions.loc[sessions["is_converting_session"] == 1, "user_id"])
    assert flagged == conv_users
    # Exactly one closing session per converting user.
    per_user = (
        sessions[sessions["is_converting_session"] == 1].groupby("user_id").size()
    )
    assert (per_user == 1).all()


def test_converting_session_is_the_last_touch(data):
    sessions = data("fact_sessions")
    closing = sessions[sessions["is_converting_session"] == 1]
    assert (closing["touch_position"] == closing["touches_in_journey"]).all()


def test_ground_truth_is_bounded_and_ordered(data):
    truth = data("ground_truth_incrementality")
    assert len(truth) == len(C.CHANNELS)
    assert truth["true_incremental_share"].between(0, 1).all()
    assert truth["true_incremental_share"].sum() == pytest.approx(1.0, abs=1e-5)
    # A channel cannot be incremental for more conversions than it was present in.
    assert (truth["true_incremental_conversions"] <= truth["conversions_touched"]).all()
    assert (truth["conversions_touched"] <= truth["users_touched"]).all()


def test_true_lift_ordering_survives_into_incrementality(data):
    """
    Higher planted lift should mean more incremental conversions, once exposure
    is held roughly constant. Rank correlation is the honest form of that claim:
    display gets an enormous number of impressions at a tiny lift, so a strict
    ordering would be wrong.
    """
    truth = data("ground_truth_incrementality")
    r = truth["true_lift"].rank().corr(truth["true_incremental_conversions"].rank())
    assert r > 0.5, f"planted lift barely shows up in the outcome (rank r={r:.2f})"


def test_direct_is_the_planted_trap(data):
    """
    The whole repo turns on this: direct closes a lot and causes almost nothing.
    If a future change breaks it, the README's headline stops being true.
    """
    truth = data("ground_truth_incrementality").set_index("channel")
    sessions = data("fact_sessions")
    closers = sessions[sessions["is_converting_session"] == 1]["channel"].value_counts(
        normalize=True
    )
    assert closers["direct"] > 0.15, "direct should dominate closing touches"
    assert truth.loc["direct", "true_incremental_share"] < 0.05, (
        "direct should be nearly non-incremental"
    )


def test_conversion_probability_is_monotone_and_bounded():
    """The noisy-OR must never leave [baseline, 1) and must never decrease."""
    lifts = np.array([C.CHANNELS[c]["true_lift"] for c in C.CHANNEL_NAMES])
    zero = np.zeros((1, len(lifts)))
    assert conversion_probability(zero, lifts)[0] == pytest.approx(C.BASELINE_CONVERSION)

    counts = np.zeros((5, len(lifts)))
    counts[:, 0] = np.arange(5)
    p = conversion_probability(counts, lifts)
    assert np.all(np.diff(p) >= 0)
    assert p.max() < 1.0


def test_touch_cap_is_enforced():
    """Beyond the cap, extra touches buy nothing — otherwise retargeting is magic."""
    lifts = np.array([C.CHANNELS[c]["true_lift"] for c in C.CHANNEL_NAMES])
    counts = np.zeros((2, len(lifts)))
    counts[0, 0] = C.MAX_EFFECTIVE_TOUCHES
    counts[1, 0] = C.MAX_EFFECTIVE_TOUCHES + 40
    p = conversion_probability(counts, lifts)
    assert p[0] == pytest.approx(p[1])


def test_spend_only_on_paid_channels(data):
    spend, channels = data("fact_spend"), data("dim_channel")
    paid = set(channels.loc[channels["is_paid"] == 1, "channel"])
    unpaid_spend = spend.loc[~spend["channel"].isin(paid), "spend"].sum()
    assert unpaid_spend == 0
    assert spend.loc[spend["channel"].isin(paid), "spend"].sum() > 0


def test_orders_reconcile_to_converting_users(data):
    users, orders = data("dim_user"), data("fact_orders")
    first_orders = orders[orders["is_first_order"] == 1]
    assert first_orders["user_id"].is_unique
    assert set(first_orders["user_id"]) == set(users.loc[users["converted"] == 1, "user_id"])
    assert (orders["revenue"] > 0).all()
    assert (orders["gross_margin"] < orders["revenue"]).all()


def test_experiment_is_a_separate_dataset(data):
    """
    The geo test must not be a slice of the observational tables — if it were,
    suppressing a channel would contaminate the attribution bake-off.
    """
    exp = data("experiment_geo_weekly")
    assert set(exp["geo"]) == set(C.EXPERIMENT_GEOS)
    assert "user_id" not in exp.columns
    held = exp[exp["in_holdout"] == 1]
    # Suppression happens in the treatment period only, and only in the holdout.
    assert (held.loc[held["period"] == "pre", "channel_suppressed"] == 0).all()
    assert (held.loc[held["period"] == "treatment", "channel_suppressed"] == 1).all()
    assert (exp.loc[exp["in_holdout"] == 0, "channel_suppressed"] == 0).all()
    assert (exp["conversions"] <= exp["sessions"]).all()


def test_generation_is_deterministic(tmp_path, data):
    """Same seed, same bytes. Without this, nothing else in the suite means much."""
    before = (ROOT / "data" / "ground_truth_incrementality.csv").read_bytes()
    import subprocess

    subprocess.run(
        [sys.executable, "data_generator/generate_marketing_data.py"],
        cwd=ROOT, capture_output=True, check=True,
    )
    after = (ROOT / "data" / "ground_truth_incrementality.csv").read_bytes()
    assert before == after
