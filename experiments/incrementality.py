"""
The geo holdout: the only thing in this repo that actually measures causality.

Every model in `attribution/` reads observational data and, as the bake-off
shows, none of them recovers the truth — because they all read how often a
channel is *present*, and presence is not effect. An experiment breaks that
because the researcher, not the customer, decides who sees the channel.

Three things happen here, in the order a real readout should:

1. **Power first.** What effect could this test detect at all? Computed before
   looking at the result, because a post-hoc power calculation on an observed
   effect is circular and tells you nothing.
2. **The naive estimate.** Compare the holdout regions before and after. It is
   wrong, and it is wrong in the direction that a seasonal upswing pushes it,
   which is exactly how these tests get misread in practice.
3. **Difference-in-differences.** Subtract the control regions' own change over
   the same weeks. That is the estimate, with a bootstrap interval.

Only at the very end do we compare the recovered lift to the planted truth in
`config.py` — the check that the method works, not an input to it.

    python experiments/incrementality.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402

# Normal quantiles for the two standard confidence levels, hard-coded so the
# module stays dependency-light (no scipy for two constants).
Z_ALPHA_TWO_SIDED = 1.959964   # alpha = 0.05
Z_POWER = 0.841621             # power = 0.80


def required_sample_per_arm(
    baseline_cvr: float, relative_lift: float, alpha_z: float = Z_ALPHA_TWO_SIDED,
    power_z: float = Z_POWER,
) -> int:
    """
    Two-proportion sample size. Returns sessions needed *per arm*.

        n = 2 (z_a + z_b)^2 p(1-p) / delta^2
    """
    delta = baseline_cvr * relative_lift
    if delta <= 0:
        return 0
    p = baseline_cvr
    return int(np.ceil(2 * (alpha_z + power_z) ** 2 * p * (1 - p) / delta**2))


def minimum_detectable_effect(
    baseline_cvr: float, n_per_arm: int, alpha_z: float = Z_ALPHA_TWO_SIDED,
    power_z: float = Z_POWER,
) -> float:
    """The smallest relative lift this test could have found. The inverse of above."""
    p = baseline_cvr
    delta = (alpha_z + power_z) * np.sqrt(2 * p * (1 - p) / n_per_arm)
    return float(delta / p)


def _rates(df: pd.DataFrame) -> float:
    return float(df["conversions"].sum() / df["sessions"].sum())


def naive_pre_post(df: pd.DataFrame) -> dict:
    """Holdout regions, before vs after. The estimate that looks obvious and isn't."""
    held = df[df["in_holdout"] == 1]
    pre, post = _rates(held[held["period"] == "pre"]), _rates(held[held["period"] == "treatment"])
    return {
        "estimator": "naive pre/post (holdout only)",
        "pre_cvr": pre,
        "post_cvr": post,
        "estimated_lift": (pre - post) / pre,
    }


def geo_ratios(df: pd.DataFrame) -> pd.DataFrame:
    """
    Each geo's own post/pre conversion-rate ratio.

    Normalising *within* geo before comparing arms is what makes this estimator
    usable. Geos differ in baseline conversion by more than the effect being
    measured, so pooling raw rates across geos buries a 5% signal under a 40%
    spread in geo quality. Every geo becomes its own control for its own level,
    and only the *change* is compared across arms.
    """
    agg = (
        df.groupby(["geo", "in_holdout", "period"], as_index=False)[["sessions", "conversions"]]
        .sum()
    )
    agg["cvr"] = agg["conversions"] / agg["sessions"]
    wide = agg.pivot_table(
        index=["geo", "in_holdout"], columns="period", values="cvr"
    ).reset_index()
    wide["ratio"] = wide["treatment"] / wide["pre"]
    return wide


def difference_in_differences(df: pd.DataFrame) -> dict:
    """
    The real estimate. Both arms move with the season; only the holdout also
    loses the channel, so the ratio of the two mean ratios isolates it.
    """
    wide = geo_ratios(df)
    held = wide[wide["in_holdout"] == 1]
    ctrl = wide[wide["in_holdout"] == 0]
    did_ratio = held["ratio"].mean() / ctrl["ratio"].mean()
    return {
        "estimator": "difference-in-differences (within-geo)",
        "holdout_pre_cvr": float(
            _rates(df[(df["in_holdout"] == 1) & (df["period"] == "pre")])
        ),
        "holdout_post_cvr": float(
            _rates(df[(df["in_holdout"] == 1) & (df["period"] == "treatment")])
        ),
        "control_pre_cvr": float(
            _rates(df[(df["in_holdout"] == 0) & (df["period"] == "pre")])
        ),
        "control_post_cvr": float(
            _rates(df[(df["in_holdout"] == 0) & (df["period"] == "treatment")])
        ),
        "holdout_mean_ratio": float(held["ratio"].mean()),
        "control_mean_ratio": float(ctrl["ratio"].mean()),
        "estimated_lift": float(1.0 - did_ratio),
    }


def bootstrap_did(df: pd.DataFrame, n_boot: int = 2000, seed: int = 99) -> tuple[float, float]:
    """
    Resample geo-weeks within each arm. The geo is the unit of assignment, so
    resampling individual sessions would understate the interval by pretending
    thousands of sessions in one geo are thousands of independent decisions.
    This is why the interval narrows by adding geos, not by adding traffic.
    """
    rng = np.random.default_rng(seed)
    wide = geo_ratios(df)
    held = wide.loc[wide["in_holdout"] == 1, "ratio"].to_numpy()
    ctrl = wide.loc[wide["in_holdout"] == 0, "ratio"].to_numpy()

    estimates = []
    for _ in range(n_boot):
        h = held[rng.integers(0, len(held), size=len(held))]
        c = ctrl[rng.integers(0, len(ctrl), size=len(ctrl))]
        estimates.append(1.0 - (h.mean() / c.mean()))
    lo, hi = np.percentile(estimates, [2.5, 97.5])
    return float(lo), float(hi)


def main() -> None:
    df = pd.read_csv(C.DATA / "experiment_geo_weekly.csv")
    true_lift = C.CHANNELS[C.EXPERIMENT_CHANNEL]["true_lift"]
    baseline = _rates(df[df["period"] == "pre"])

    held = df[df["in_holdout"] == 1]
    n_per_arm = int(held[held["period"] == "treatment"]["sessions"].sum())

    print(f"Geo holdout on {C.EXPERIMENT_CHANNEL}")
    print(f"  holdout geos    : {', '.join(C.EXPERIMENT_HOLDOUT_GEOS)} of {len(C.EXPERIMENT_GEOS)}")
    print(f"  baseline CVR    : {baseline:.4f}")
    print(f"  sessions in the treated arm: {n_per_arm:,}")

    print("\n--- 1. Power, computed before looking at the result")
    mde = minimum_detectable_effect(baseline, n_per_arm)
    print(f"  minimum detectable relative lift at 80% power: {mde:.2%}")
    for ch in ("paid_search", "paid_social"):
        need = required_sample_per_arm(baseline, C.CHANNELS[ch]["true_lift"])
        verdict = "testable" if need <= n_per_arm else "NOT testable at this traffic"
        print(
            f"  detecting {ch:<13} (lift {C.CHANNELS[ch]['true_lift']:.3f}) "
            f"needs {need:>9,} sessions/arm - {verdict}"
        )

    print("\n--- 2. The naive readout")
    naive = naive_pre_post(df)
    print(
        f"  holdout CVR {naive['pre_cvr']:.4f} -> {naive['post_cvr']:.4f}  "
        f"=> estimated lift {naive['estimated_lift']:.2%}"
    )

    print("\n--- 3. Difference-in-differences")
    did = difference_in_differences(df)
    lo, hi = bootstrap_did(df)
    print(
        f"  holdout {did['holdout_pre_cvr']:.4f} -> {did['holdout_post_cvr']:.4f}   "
        f"control {did['control_pre_cvr']:.4f} -> {did['control_post_cvr']:.4f}"
    )
    print(
        f"  estimated incremental lift: {did['estimated_lift']:.2%}  "
        f"(95% CI {lo:.2%} to {hi:.2%})"
    )

    print("\n--- Grading against the planted truth")
    print(f"  true lift            : {true_lift:.2%}")
    print(f"  DiD estimate         : {did['estimated_lift']:.2%}")
    print(f"  naive estimate       : {naive['estimated_lift']:.2%}")
    covered = lo <= true_lift <= hi
    print(f"  truth inside the DiD 95% interval: {covered}")

    rows = [
        {
            "estimator": naive["estimator"],
            "estimated_lift": round(naive["estimated_lift"], 6),
            "ci_low": None,
            "ci_high": None,
            "true_lift": true_lift,
            "abs_error": round(abs(naive["estimated_lift"] - true_lift), 6),
            "covers_truth": None,
        },
        {
            "estimator": did["estimator"],
            "estimated_lift": round(did["estimated_lift"], 6),
            "ci_low": round(lo, 6),
            "ci_high": round(hi, 6),
            "true_lift": true_lift,
            "abs_error": round(abs(did["estimated_lift"] - true_lift), 6),
            "covers_truth": bool(covered),
        },
    ]
    C.OUT.mkdir(parents=True, exist_ok=True)
    out = pd.DataFrame(rows)
    out.to_csv(C.OUT / "incrementality_readout.csv", index=False, lineterminator="\n")
    pd.DataFrame(
        [
            {
                "baseline_cvr": round(baseline, 6),
                "sessions_treated_arm": n_per_arm,
                "mde_relative": round(mde, 6),
                "required_n_paid_search": required_sample_per_arm(
                    baseline, C.CHANNELS["paid_search"]["true_lift"]
                ),
                "required_n_paid_social": required_sample_per_arm(
                    baseline, C.CHANNELS["paid_social"]["true_lift"]
                ),
            }
        ]
    ).to_csv(C.OUT / "experiment_power.csv", index=False, lineterminator="\n")
    print("\n  wrote output/incrementality_readout.csv and output/experiment_power.csv")


if __name__ == "__main__":
    main()
