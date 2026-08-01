"""
The attribution models, and the honesty of the bake-off that grades them.

Several of these tests are written to fail if the harness ever starts leaking
the answer to the models — a model that scored suspiciously well would be a
bug, not a result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402
from attribution.evaluate import MODELS  # noqa: E402
from attribution.models import (  # noqa: E402
    build_transition_matrix,
    conversion_probability,
    markov_removal_effect,
    shapley_values,
)


# ------------------------------------------------------------------- markov
def test_transition_matrix_rows_are_distributions():
    paths = [["a", "b"], ["b"], ["a", "a", "b"]]
    probs, states = build_transition_matrix(paths, [1, 0, 1], ["a", "b"])
    row_sums = probs.sum(axis=1)
    assert np.allclose(row_sums, 1.0)
    assert set(states) == {"(start)", "a", "b", "(conversion)", "(null)"}


def test_absorbing_states_are_absorbing():
    probs, states = build_transition_matrix([["a"]], [1], ["a"])
    for name in ("(conversion)", "(null)"):
        i = states.index(name)
        assert probs[i, i] == pytest.approx(1.0)


def test_conversion_probability_recovers_a_known_chain():
    """Three journeys, two convert: the chain must say 2/3."""
    paths = [["a"], ["a"], ["a"]]
    probs, states = build_transition_matrix(paths, [1, 1, 0], ["a"])
    assert conversion_probability(probs, states) == pytest.approx(2 / 3)


def test_removing_the_only_channel_removes_all_conversions():
    paths = [["a"], ["a"], ["a"]]
    df = markov_removal_effect(paths, [1, 1, 0], ["a"])
    assert df.loc[0, "removal_effect"] == pytest.approx(1.0)
    assert df.loc[0, "markov_share"] == pytest.approx(1.0)


def test_markov_shares_sum_to_one(out):
    cmp_df = out("attribution_comparison")
    assert cmp_df["markov"].sum() == pytest.approx(1.0, abs=1e-4)
    assert (cmp_df["markov"] >= 0).all()


# ------------------------------------------------------------------ shapley
def test_shapley_is_efficient_on_a_toy_game():
    """
    Two channels that only ever appear together: Shapley must split the
    coalition's worth evenly, because neither does anything alone.
    """
    paths = [["a", "b"]] * 10
    df = shapley_values(paths, [1] * 10, ["a", "b"]).set_index("channel")
    assert df.loc["a", "shapley_share"] == pytest.approx(0.5)
    assert df.loc["b", "shapley_share"] == pytest.approx(0.5)


def test_shapley_gives_nothing_to_a_channel_that_never_converts():
    paths = [["a"]] * 8 + [["b"]] * 8
    converted = [1] * 8 + [0] * 8
    df = shapley_values(paths, converted, ["a", "b"]).set_index("channel")
    assert df.loc["b", "shapley_value"] == pytest.approx(0.0)
    assert df.loc["a", "shapley_share"] == pytest.approx(1.0)


def test_shapley_total_equals_total_conversions():
    """Efficiency axiom: the values must add up to the grand coalition's worth."""
    paths = [["a", "b"], ["b", "c"], ["a"], ["a", "b", "c"], ["c"]]
    converted = [1, 1, 0, 1, 1]
    df = shapley_values(paths, converted, ["a", "b", "c"])
    assert df["shapley_value"].sum() == pytest.approx(sum(converted))


# -------------------------------------------------------------- the bake-off
def test_every_model_produces_a_valid_split(out):
    cmp_df = out("attribution_comparison")
    for m in MODELS:
        assert cmp_df[m].sum() == pytest.approx(1.0, abs=1e-3), f"{m} does not sum to 1"
        assert (cmp_df[m] >= -1e-9).all(), f"{m} has negative credit"


def test_no_model_is_suspiciously_perfect(out):
    """
    Attribution shares sum to 1; incremental truth does not. Even a perfect
    model therefore carries irreducible error, so a near-zero score would mean
    the ground truth had leaked into the models rather than that one is superb.
    """
    scores = out("attribution_scores")
    assert scores["mae_share_points"].min() > 0.5, (
        "a model scored almost perfectly — check the harness for a leak"
    )


def test_models_never_see_the_true_lift():
    """Static guard: the model module must not import or read the truth."""
    src = (ROOT / "attribution" / "models.py").read_text(encoding="utf-8").lower()
    for forbidden in ("true_lift", "ground_truth", "incremental_share"):
        assert forbidden not in src, f"models.py references {forbidden}"


def test_last_touch_massively_overcredits_direct(out):
    """The repo's headline claim, enforced."""
    cmp_df = out("attribution_comparison").set_index("channel")
    lt = cmp_df.loc["direct", "last_touch"]
    truth = cmp_df.loc["direct", "true_incremental_share"]
    assert lt > 0.15, f"last-touch gives direct only {lt:.1%}"
    assert truth < 0.05, f"direct's true share is {truth:.1%}"
    assert lt / truth > 5, "the overcredit ratio is no longer dramatic"


def test_last_touch_is_the_worst_model_on_share_error(out):
    scores = out("attribution_scores").set_index("model")
    assert scores["mae_share_points"].idxmax() == "last_touch"


def test_the_cheap_heuristic_beats_the_expensive_models(out):
    """
    The finding the README leads with. Position-based needs no library and no
    training; if a future change makes Markov or Shapley win, the README is
    wrong and this test should say so.
    """
    scores = out("attribution_scores").set_index("model")["mae_share_points"]
    assert scores["position_based"] < scores["markov"]
    assert scores["position_based"] < scores["shapley"]


def test_budget_misallocation_is_bounded_by_the_budget(out):
    b = out("budget_misallocation")
    assert (b["dollars_misallocated"] >= 0).all()
    assert (b["dollars_misallocated"] <= b["paid_budget"]).all()
    assert (b["pct_of_budget"] <= 100).all()


def test_last_touch_ranks_better_on_budget_than_on_share(out):
    """
    The nuance the README refuses to drop: last-touch is the worst model
    overall and *not* the worst for splitting paid budget, because its biggest
    error lands on a channel nobody can buy.
    """
    scores = out("attribution_scores").set_index("model")
    budget = out("budget_misallocation").set_index("model")
    share_rank = scores["mae_share_points"].rank().loc["last_touch"]
    budget_rank = budget["dollars_misallocated"].rank().loc["last_touch"]
    assert budget_rank < share_rank


def test_channels_line_up_across_every_output(out, data):
    expected = set(C.CHANNEL_NAMES)
    assert set(out("attribution_comparison")["channel"]) == expected
    assert set(out("channel_efficiency")["channel"]) == expected
    assert set(data("ground_truth_incrementality")["channel"]) == expected
