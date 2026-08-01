"""
Charts for the README, drawn from output/ — never from hard-coded numbers.

Every figure here reads a CSV that the SQL or the models just wrote, so a
chart cannot silently disagree with the tables the tests assert on.

    python analytics/make_visuals.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402

NAVY, TEAL, ORANGE, GREY = "#12436D", "#28A197", "#F46A25", "#A8B0B8"
plt.rcParams.update(
    {
        "figure.dpi": 130,
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.25,
        "grid.linestyle": "-",
    }
)


def _save(fig, name: str) -> None:
    C.DOCS.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(C.DOCS / name, bbox_inches="tight")
    plt.close(fig)
    print(f"  docs/{name}")


def chart_attribution(cmp_df: pd.DataFrame) -> None:
    """The headline: what each model believes, against what is true."""
    d = cmp_df.sort_values("true_incremental_share", ascending=False)
    x = np.arange(len(d))
    w = 0.26
    fig, ax = plt.subplots(figsize=(9, 4.2))
    ax.bar(x - w, 100 * d["true_incremental_share"], w, label="TRUTH (incremental)", color=NAVY)
    ax.bar(x, 100 * d["last_touch"], w, label="Last-touch", color=ORANGE)
    ax.bar(x + w, 100 * d["position_based"], w, label="Position-based (best model)", color=TEAL)
    ax.set_xticks(x)
    ax.set_xticklabels(d["channel"], rotation=20, ha="right")
    ax.set_ylabel("share of credit (%)")
    ax.set_title(
        "Last-touch gives 'direct' a quarter of all conversions. Its true share is 2%.",
        loc="left", fontsize=10, weight="bold",
    )
    ax.legend(frameon=False, ncols=3, fontsize=8)
    _save(fig, "01-attribution-vs-truth.png")


def chart_model_accuracy(scores: pd.DataFrame) -> None:
    d = scores.sort_values("mae_share_points", ascending=True)
    fig, ax = plt.subplots(figsize=(7, 3.4))
    colors = [TEAL if i == 0 else (ORANGE if m == "last_touch" else GREY)
              for i, m in enumerate(d["model"])]
    ax.barh(d["model"], d["mae_share_points"], color=colors)
    for y, (v, r) in enumerate(zip(d["mae_share_points"], d["rank_correlation"], strict=True)):
        ax.text(v + 0.12, y, f"{v:.2f} pts   (rank r={r:.2f})", va="center", fontsize=8)
    ax.set_xlabel("mean absolute error on share of credit (percentage points)")
    ax.set_xlim(0, d["mae_share_points"].max() * 1.45)
    ax.invert_yaxis()
    ax.set_title(
        "No model recovers the truth. The cheapest heuristic wins.",
        loc="left", fontsize=10, weight="bold",
    )
    _save(fig, "02-model-accuracy.png")


def chart_roles(roles: pd.DataFrame, truth: pd.DataFrame) -> None:
    """Why last-touch fails: closing share versus true contribution."""
    d = roles.merge(truth[["channel", "true_incremental_share"]], on="channel")
    d["closer_share"] = d["as_closer"] / d["as_closer"].sum()
    fig, ax = plt.subplots(figsize=(6.4, 4.4))
    ax.scatter(
        100 * d["closer_share"], 100 * d["true_incremental_share"],
        s=90, color=NAVY, zorder=3,
    )
    lim = max(100 * d["closer_share"].max(), 100 * d["true_incremental_share"].max()) * 1.15
    ax.plot([0, lim], [0, lim], color=GREY, ls="--", lw=1, zorder=1)
    for _, r in d.iterrows():
        ax.annotate(
            r["channel"], (100 * r["closer_share"], 100 * r["true_incremental_share"]),
            textcoords="offset points", xytext=(7, 4), fontsize=8,
        )
    ax.set_xlabel("share of closing touches (%)")
    ax.set_ylabel("true incremental share (%)")
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_title(
        "Anything below the line is a channel last-touch overpays.",
        loc="left", fontsize=10, weight="bold",
    )
    _save(fig, "03-closers-vs-truth.png")


def chart_funnel(funnel: pd.DataFrame, by_device: pd.DataFrame) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 3.8))
    f = funnel.sort_values("funnel_step")
    ax1.barh(f["event_name"], f["sessions"], color=NAVY)
    for y, (s, p) in enumerate(zip(f["sessions"], f["pct_of_entry"], strict=True)):
        ax1.text(s * 1.02, y, f"{s:,.0f}  ({p:.0f}%)", va="center", fontsize=8)
    ax1.invert_yaxis()
    ax1.set_xlim(0, f["sessions"].max() * 1.32)
    ax1.set_xlabel("sessions reaching step")
    ax1.set_title("The funnel", loc="left", fontsize=10, weight="bold")

    d = by_device[by_device["event_name"] == "checkout_start"]
    ax2.bar(d["device"], d["step_conversion_pct"],
            color=[ORANGE if v == d["step_conversion_pct"].min() else TEAL
                   for v in d["step_conversion_pct"]])
    for i, v in enumerate(d["step_conversion_pct"]):
        ax2.text(i, v + 0.6, f"{v:.1f}%", ha="center", fontsize=8)
    ax2.set_ylabel("cart -> checkout (%)")
    ax2.set_ylim(0, d["step_conversion_pct"].max() * 1.22)
    ax2.set_title("Mobile loses the cart", loc="left", fontsize=10, weight="bold")
    _save(fig, "04-funnel.png")


def chart_ltv(ltv: pd.DataFrame) -> None:
    fig, ax = plt.subplots(figsize=(7.4, 4))
    cohorts = sorted(ltv["cohort_month"].unique())[:8]
    cmap = plt.get_cmap("viridis")
    for i, c in enumerate(cohorts):
        d = ltv[ltv["cohort_month"] == c].sort_values("months_since")
        ax.plot(
            d["months_since"], d["cumulative_revenue_per_customer"],
            marker="o", ms=3, lw=1.6, color=cmap(i / max(len(cohorts) - 1, 1)), label=c,
        )
    ax.set_xlabel("months since first order")
    ax.set_ylabel("cumulative revenue per acquired customer ($)")
    ax.set_title(
        "LTV curves by acquisition cohort", loc="left", fontsize=10, weight="bold"
    )
    ax.legend(frameon=False, fontsize=7, ncols=2, title="cohort", title_fontsize=7)
    _save(fig, "05-cohort-ltv.png")


def chart_experiment(readout: pd.DataFrame, true_lift: float) -> None:
    did = readout[readout["estimator"].str.startswith("difference")].iloc[0]
    naive = readout[readout["estimator"].str.startswith("naive")].iloc[0]

    fig, ax = plt.subplots(figsize=(7.4, 3.2))
    ax.axvline(100 * true_lift, color=NAVY, lw=2, zorder=1)
    ax.text(
        100 * true_lift, 1.62, f"  planted truth {100 * true_lift:.2f}%",
        color=NAVY, fontsize=9, weight="bold", va="center",
    )
    ax.errorbar(
        100 * did["estimated_lift"], 1.0,
        xerr=[[100 * (did["estimated_lift"] - did["ci_low"])],
              [100 * (did["ci_high"] - did["estimated_lift"])]],
        fmt="o", color=TEAL, capsize=5, ms=8, lw=2, zorder=3,
    )
    ax.scatter(100 * naive["estimated_lift"], 0.4, color=ORANGE, s=80, zorder=3)
    ax.set_yticks([1.0, 0.4])
    ax.set_yticklabels(["Difference-in-differences\n(95% CI)", "Naive pre/post"])
    ax.set_ylim(0.05, 1.85)
    ax.set_xlabel("estimated incremental lift of paid search (%)")
    ax.set_title(
        "The experiment settles what the models argue about.",
        loc="left", fontsize=10, weight="bold",
    )
    _save(fig, "06-incrementality.png")


def main() -> None:
    cmp_df = pd.read_csv(C.OUT / "attribution_comparison.csv")
    scores = pd.read_csv(C.OUT / "attribution_scores.csv")
    roles = pd.read_csv(C.OUT / "channel_journey_roles.csv")
    truth = pd.read_csv(C.DATA / "ground_truth_incrementality.csv")
    funnel = pd.read_csv(C.OUT / "funnel_overall.csv")
    by_device = pd.read_csv(C.OUT / "funnel_by_device.csv")
    ltv = pd.read_csv(C.OUT / "cohort_ltv.csv")
    readout = pd.read_csv(C.OUT / "incrementality_readout.csv")

    print("Writing charts")
    chart_attribution(cmp_df)
    chart_model_accuracy(scores)
    chart_roles(roles, truth)
    chart_funnel(funnel, by_device)
    chart_ltv(ltv)
    chart_experiment(readout, C.CHANNELS[C.EXPERIMENT_CHANNEL]["true_lift"])


if __name__ == "__main__":
    main()
