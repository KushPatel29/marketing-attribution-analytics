"""
The experiment: does the readout actually recover a known effect?

The strongest tests here plant an effect of a size we choose and demand the
estimator find it — including planting *no* effect and demanding it finds
nothing, which is the half people skip.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from experiments.incrementality import (  # noqa: E402
    bootstrap_did,
    difference_in_differences,
    minimum_detectable_effect,
    naive_pre_post,
    required_sample_per_arm,
)


def synthetic_geo_panel(true_lift: float, seed: int = 7, n_geos: int = 20) -> pd.DataFrame:
    """A geo panel with a known effect, built independently of the generator."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_geos):
        held = g % 2 == 1
        base = float(rng.uniform(0.030, 0.050))
        scale = float(rng.uniform(0.8, 1.3))
        for week in range(16):
            treat = week >= 8
            season = 1.0 + 0.06 * np.sin(week / 2.2)
            cvr = base * season * ((1 - true_lift) if (held and treat) else 1.0)
            n = int(6000 * scale)
            rows.append(
                {
                    "geo": f"G{g:02d}",
                    "week": week,
                    "period": "treatment" if treat else "pre",
                    "in_holdout": int(held),
                    "sessions": n,
                    "conversions": int(rng.binomial(n, cvr)),
                }
            )
    return pd.DataFrame(rows)


# ------------------------------------------------------------------- power
def test_required_sample_grows_as_the_effect_shrinks():
    big = required_sample_per_arm(0.04, 0.10)
    small = required_sample_per_arm(0.04, 0.02)
    assert small > big
    # Quadratic in the effect size: halving the lift roughly quadruples n.
    assert required_sample_per_arm(0.04, 0.05) / required_sample_per_arm(0.04, 0.10) == (
        pytest.approx(4, rel=0.05)
    )


def test_mde_and_required_sample_are_inverses():
    n = required_sample_per_arm(0.04, 0.055)
    assert minimum_detectable_effect(0.04, n) == pytest.approx(0.055, rel=0.01)


def test_paid_social_is_correctly_declared_untestable(out):
    """
    The power table's purpose: say out loud which channels this traffic cannot
    measure, rather than running the test and reporting a null as a finding.
    """
    p = out("experiment_power").iloc[0]
    assert p["required_n_paid_social"] > p["sessions_treated_arm"]
    assert p["required_n_paid_search"] <= p["sessions_treated_arm"]


# ------------------------------------------------------------- estimators
def test_did_recovers_a_planted_effect():
    panel = synthetic_geo_panel(true_lift=0.08)
    est = difference_in_differences(panel)["estimated_lift"]
    assert est == pytest.approx(0.08, abs=0.02)


def test_did_finds_nothing_when_there_is_nothing():
    """The half of validation that gets skipped: a null must read as null."""
    panel = synthetic_geo_panel(true_lift=0.0)
    est = difference_in_differences(panel)["estimated_lift"]
    lo, hi = bootstrap_did(panel, n_boot=600, seed=3)
    assert abs(est) < 0.02
    assert lo <= 0.0 <= hi, "the interval should cover zero when no effect exists"


def test_naive_estimator_is_fooled_by_the_common_shock():
    """
    Both arms fall together on seasonality; the naive readout charges all of it
    to the channel. This test asserts the bias exists, because the README uses
    it to argue for difference-in-differences.
    """
    panel = synthetic_geo_panel(true_lift=0.05)
    naive = naive_pre_post(panel)["estimated_lift"]
    did = difference_in_differences(panel)["estimated_lift"]
    assert abs(naive - 0.05) > abs(did - 0.05), "the naive estimate should be worse"


def test_bootstrap_interval_covers_the_planted_truth_across_seeds():
    """
    One interval covering the truth could be luck. Across ten independent
    panels a 95% interval should cover almost always — this asserts at least
    eight, which is the honest bar for ten draws.
    """
    covered = 0
    for seed in range(10):
        panel = synthetic_geo_panel(true_lift=0.06, seed=100 + seed)
        lo, hi = bootstrap_did(panel, n_boot=500, seed=seed)
        covered += int(lo <= 0.06 <= hi)
    assert covered >= 8, f"only {covered}/10 intervals covered the truth"


def test_interval_narrows_with_more_geos():
    """
    The design lesson encoded as a test: the interval is driven by the number
    of randomised units, not by traffic inside them.
    """
    narrow = bootstrap_did(synthetic_geo_panel(0.06, seed=11, n_geos=40), n_boot=600, seed=1)
    wide = bootstrap_did(synthetic_geo_panel(0.06, seed=11, n_geos=8), n_boot=600, seed=1)
    assert (narrow[1] - narrow[0]) < (wide[1] - wide[0])


# ------------------------------------------------------------- the readout
def test_shipped_readout_covers_the_truth(out):
    r = out("incrementality_readout")
    did = r[r["estimator"].str.startswith("difference")].iloc[0]
    true_lift = C.CHANNELS[C.EXPERIMENT_CHANNEL]["true_lift"]
    assert did["ci_low"] <= true_lift <= did["ci_high"]
    assert bool(did["covers_truth"])


def test_shipped_naive_estimate_is_the_worse_one(out):
    r = out("incrementality_readout").set_index("estimator")
    naive = r[r.index.str.startswith("naive")].iloc[0]
    did = r[r.index.str.startswith("difference")].iloc[0]
    assert naive["abs_error"] > did["abs_error"]


def test_experiment_interval_excludes_zero(out):
    """
    A test that cannot rule out zero has not settled anything. If this fails,
    the README must stop saying the experiment settles the question.
    """
    r = out("incrementality_readout")
    did = r[r["estimator"].str.startswith("difference")].iloc[0]
    assert did["ci_low"] > 0
