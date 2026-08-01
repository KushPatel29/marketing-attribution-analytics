"""
Grade every attribution model against the planted incremental truth.

Six models compete: four heuristics computed in SQL (first-touch, last-touch,
linear, position-based) and two computed here (Markov removal effect, exact
Shapley). None of them ever sees `true_lift`.

The scoring metric is mean absolute error on *share of credit*, in percentage
points, plus Spearman rank correlation — because a media planner cares first
about the ordering of channels and second about the exact split.

Two honesty notes that the README repeats:

1. Incremental credit does not sum to 1; attribution shares do. Comparing them
   requires normalising the truth, which means even a hypothetically perfect
   model carries irreducible error. No model here can score zero, and one that
   did would mean the harness was leaking.

2. The budget-reallocation number at the end is an *arithmetic* consequence of
   the share error, not a forecast. Spending against truth instead of
   last-touch would not mechanically produce that revenue; it is the size of
   the misallocation, which is a different and more defensible claim.

    python attribution/evaluate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
from attribution.models import (  # noqa: E402
    load_journeys,
    markov_removal_effect,
    shapley_values,
)

MODELS = [
    "first_touch",
    "last_touch",
    "linear",
    "position_based",
    "markov",
    "shapley",
]


def spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank(), method="pearson"))


def build_share_table() -> pd.DataFrame:
    """One row per channel, one column per model, all shares summing to 1."""
    heur = pd.read_csv(C.OUT / "attribution_heuristics.csv")
    paths, converted = load_journeys(C.DATA / "fact_journeys.csv")
    channels = pd.read_csv(C.DATA / "dim_channel.csv")["channel"].tolist()

    markov = markov_removal_effect(paths, converted, channels)
    shap = shapley_values(paths, converted, channels)

    df = heur[
        [
            "channel",
            "first_touch_share",
            "last_touch_share",
            "linear_share",
            "position_based_share",
        ]
    ].copy()
    df.columns = ["channel", "first_touch", "last_touch", "linear", "position_based"]
    df = df.merge(markov[["channel", "markov_share"]], on="channel")
    df = df.merge(shap[["channel", "shapley_share"]], on="channel")
    df = df.rename(columns={"markov_share": "markov", "shapley_share": "shapley"})

    truth = pd.read_csv(C.DATA / "ground_truth_incrementality.csv")
    df = df.merge(
        truth[["channel", "true_lift", "true_incremental_share"]], on="channel"
    )
    return df.sort_values("true_incremental_share", ascending=False, ignore_index=True)


def score(df: pd.DataFrame) -> pd.DataFrame:
    truth = df["true_incremental_share"]
    rows = []
    for m in MODELS:
        err = (df[m] - truth).abs()
        rows.append(
            {
                "model": m,
                "mae_share_points": round(100 * err.mean(), 3),
                "max_error_points": round(100 * err.max(), 3),
                "worst_channel": df.loc[err.idxmax(), "channel"],
                "rank_correlation": round(spearman(df[m], truth), 3),
                "share_sums_to": round(df[m].sum(), 6),
            }
        )
    return pd.DataFrame(rows).sort_values("mae_share_points", ignore_index=True)


def budget_misallocation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Translate share error into dollars: if the paid budget were split by each
    model's shares, how far is that from the split truth implies?
    """
    eff = pd.read_csv(C.OUT / "channel_efficiency.csv")
    paid_budget = float(eff.loc[eff["is_paid"] == 1, "spend"].sum())
    paid = set(eff.loc[eff["is_paid"] == 1, "channel"])

    sub = df[df["channel"].isin(paid)].copy()
    rows = []
    for m in MODELS:
        model_split = sub[m] / sub[m].sum()
        true_split = sub["true_incremental_share"] / sub["true_incremental_share"].sum()
        misallocated = (model_split - true_split).abs().sum() / 2 * paid_budget
        rows.append(
            {
                "model": m,
                "paid_budget": round(paid_budget, 2),
                "dollars_misallocated": round(misallocated, 2),
                "pct_of_budget": round(100 * misallocated / paid_budget, 2),
            }
        )
    return pd.DataFrame(rows).sort_values("dollars_misallocated", ignore_index=True)


def main() -> None:
    C.OUT.mkdir(parents=True, exist_ok=True)
    df = build_share_table()
    scores = score(df)
    budget = budget_misallocation(df)

    df.to_csv(C.OUT / "attribution_comparison.csv", index=False, lineterminator="\n")
    scores.to_csv(C.OUT / "attribution_scores.csv", index=False, lineterminator="\n")
    budget.to_csv(C.OUT / "budget_misallocation.csv", index=False, lineterminator="\n")

    pd.set_option("display.width", 150)
    print("Share of credit by model, against the planted truth\n")
    show = df[["channel", "true_incremental_share", *MODELS]].copy()
    for c in show.columns[1:]:
        show[c] = (100 * show[c]).round(1)
    show = show.rename(columns={"true_incremental_share": "TRUTH"})
    print(show.to_string(index=False))

    print("\n\nAccuracy (mean absolute error on share, percentage points)\n")
    print(scores.to_string(index=False))

    best, worst = scores.iloc[0], scores.iloc[-1]
    print(
        f"\n  Best: {best['model']} at {best['mae_share_points']:.2f} points MAE.  "
        f"Worst: {worst['model']} at {worst['mae_share_points']:.2f}."
    )

    print("\n\nBudget consequence (paid channels only)\n")
    print(budget.to_string(index=False))


if __name__ == "__main__":
    main()
