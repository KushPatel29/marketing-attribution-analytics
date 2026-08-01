"""
Marketing analytics console.

    streamlit run app/streamlit_app.py

Reads only the CSVs in output/ and data/, which the pipeline writes and the
test suite asserts on — so anything shown here is a number CI has already
checked. Nothing is recomputed in the UI layer, because a dashboard that
re-derives its own metrics is a second implementation waiting to disagree
with the first.
"""

from __future__ import annotations

import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402

st.set_page_config(page_title="Marketing Attribution", page_icon="📈", layout="wide")

NAVY, TEAL, ORANGE = "#12436D", "#28A197", "#F46A25"
MODEL_LABELS = {
    "first_touch": "First touch",
    "last_touch": "Last touch",
    "linear": "Linear",
    "position_based": "Position-based (40/20/40)",
    "markov": "Markov removal effect",
    "shapley": "Shapley value",
}


@st.cache_data
def load(name: str, folder: str = "output") -> pd.DataFrame:
    return pd.read_csv(ROOT / folder / f"{name}.csv")


st.title("📈 Marketing Attribution & Incrementality")
st.caption(
    "Synthetic e-commerce data with a **planted ground truth** — the real "
    "incremental contribution of every channel is known, so attribution models "
    "can be graded instead of argued about."
)

try:
    cmp_df = load("attribution_comparison")
    scores = load("attribution_scores")
    budget = load("budget_misallocation")
    roles = load("channel_journey_roles")
    eff = load("channel_efficiency")
    funnel = load("funnel_overall")
    device = load("funnel_by_device")
    ltv = load("cohort_ltv")
    retention = load("cohort_retention")
    readout = load("incrementality_readout")
    power = load("experiment_power")
except FileNotFoundError:
    st.error(
        "No outputs found. Run the pipeline first:\n\n"
        "```\npython data_generator/generate_marketing_data.py\n"
        "python engine/run_analytics.py\n"
        "python attribution/evaluate.py\n"
        "python experiments/incrementality.py\n```"
    )
    st.stop()

tab1, tab2, tab3, tab4 = st.tabs(
    ["🎯 Attribution vs truth", "🔻 Funnel", "👥 Cohorts & LTV", "🧪 The experiment"]
)

# ------------------------------------------------------------------- tab 1
with tab1:
    best = scores.iloc[0]
    worst = scores.iloc[-1]
    direct_lt = float(cmp_df.loc[cmp_df["channel"] == "direct", "last_touch"].iloc[0])
    direct_true = float(
        cmp_df.loc[cmp_df["channel"] == "direct", "true_incremental_share"].iloc[0]
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Best model", MODEL_LABELS[best["model"]], f"{best['mae_share_points']:.2f} pts MAE")
    c2.metric("Worst model", MODEL_LABELS[worst["model"]],
              f"{worst['mae_share_points']:.2f} pts MAE", delta_color="inverse")
    c3.metric("Last-touch credit to 'direct'", f"{100 * direct_lt:.1f}%")
    c4.metric("Its true share", f"{100 * direct_true:.1f}%", delta_color="off")

    st.info(
        f"**No model recovers the truth.** The best is "
        f"*{MODEL_LABELS[best['model']]}* at {best['mae_share_points']:.2f} percentage "
        f"points of mean error — a heuristic you can write in SQL, beating both the "
        f"Markov chain and the exact Shapley value.",
        icon=":material/insights:",
    )

    picked = st.multiselect(
        "Models to compare against the planted truth",
        list(MODEL_LABELS),
        default=["last_touch", best["model"]],
        format_func=lambda m: MODEL_LABELS[m],
    )

    long = cmp_df.melt(
        id_vars=["channel"],
        value_vars=["true_incremental_share", *picked],
        var_name="series",
        value_name="share",
    )
    long["series"] = long["series"].map(
        lambda s: "TRUTH (incremental)" if s == "true_incremental_share" else MODEL_LABELS[s]
    )
    order = cmp_df.sort_values("true_incremental_share", ascending=False)["channel"].tolist()
    # Pin the scale explicitly: truth keeps the same colour whatever the reader
    # selects, and the series stay in a stable left-to-right order.
    series_order = ["TRUTH (incremental)", *[MODEL_LABELS[m] for m in MODEL_LABELS if m in picked]]
    palette = [NAVY, ORANGE, TEAL, "#6A4C93", "#A8B0B8", "#CC2927", "#F2C811"]
    st.altair_chart(
        alt.Chart(long)
        .mark_bar()
        .encode(
            x=alt.X("channel:N", sort=order, title=None),
            y=alt.Y("share:Q", axis=alt.Axis(format="%"), title="share of credit"),
            color=alt.Color(
                "series:N",
                title=None,
                sort=series_order,
                scale=alt.Scale(domain=series_order, range=palette[: len(series_order)]),
            ),
            xOffset=alt.XOffset("series:N", sort=series_order),
            tooltip=["channel", "series", alt.Tooltip("share:Q", format=".1%")],
        )
        .properties(height=340),
        width="stretch",
    )

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Accuracy")
        show = scores.copy()
        show["model"] = show["model"].map(MODEL_LABELS)
        st.dataframe(
            show[["model", "mae_share_points", "max_error_points", "worst_channel",
                  "rank_correlation"]],
            width="stretch", hide_index=True,
        )
    with right:
        st.subheader("Where the money would go wrong")
        st.caption(
            "Paid channels only. This is the size of the misallocation implied by "
            "each model's split — not a revenue forecast."
        )
        b = budget.copy()
        b["model"] = b["model"].map(MODEL_LABELS)
        st.dataframe(
            b[["model", "dollars_misallocated", "pct_of_budget"]],
            width="stretch", hide_index=True,
        )
        st.caption(
            "Last-touch scores worst overall yet misallocates least here — its "
            "biggest error is on *direct*, a channel nobody can buy."
        )

    st.subheader("Why last-touch fails")
    r = roles.merge(cmp_df[["channel", "true_incremental_share"]], on="channel")
    r["closer_share"] = r["as_closer"] / r["as_closer"].sum()
    st.altair_chart(
        alt.Chart(r)
        .mark_circle(size=170, color=NAVY)
        .encode(
            x=alt.X("closer_share:Q", axis=alt.Axis(format="%"),
                    title="share of closing touches"),
            y=alt.Y("true_incremental_share:Q", axis=alt.Axis(format="%"),
                    title="true incremental share"),
            tooltip=["channel",
                     alt.Tooltip("closer_share:Q", format=".1%"),
                     alt.Tooltip("true_incremental_share:Q", format=".1%")],
        )
        .properties(height=320)
        + alt.Chart(r).mark_text(align="left", dx=9, fontSize=11).encode(
            x="closer_share:Q", y="true_incremental_share:Q", text="channel:N"
        ),
        width="stretch",
    )

# ------------------------------------------------------------------- tab 2
with tab2:
    f = funnel.sort_values("funnel_step")
    st.subheader("Where sessions die")
    st.altair_chart(
        alt.Chart(f)
        .mark_bar(color=NAVY)
        .encode(
            y=alt.Y("event_name:N", sort=f["event_name"].tolist(), title=None),
            x=alt.X("sessions:Q", title="sessions reaching step"),
            tooltip=["event_name", "sessions", "step_conversion_pct", "pct_of_entry"],
        )
        .properties(height=250),
        width="stretch",
    )
    st.dataframe(
        f[["funnel_step", "event_name", "sessions", "step_conversion_pct",
           "pct_of_entry", "sessions_lost"]],
        width="stretch", hide_index=True,
    )

    st.subheader("The step that differs by device")
    d = device[device["event_name"] == "checkout_start"].sort_values(
        "step_conversion_pct", ascending=False
    )
    gap = d["step_conversion_pct"].max() - d["step_conversion_pct"].min()
    st.metric("Cart → checkout gap, best vs worst device", f"{gap:.1f} points")
    st.altair_chart(
        alt.Chart(d)
        .mark_bar()
        .encode(
            x=alt.X("device:N", title=None),
            y=alt.Y("step_conversion_pct:Q", title="cart → checkout (%)"),
            color=alt.value(TEAL),
            tooltip=["device", "step_conversion_pct"],
        )
        .properties(height=280),
        width="stretch",
    )

    st.subheader("Traffic quality by entry channel")
    st.dataframe(load("funnel_by_entry_channel"), width="stretch", hide_index=True)

# ------------------------------------------------------------------- tab 3
with tab3:
    st.subheader("Cumulative revenue per acquired customer")
    st.altair_chart(
        alt.Chart(ltv)
        .mark_line(point=True)
        .encode(
            x=alt.X("months_since:Q", title="months since first order"),
            y=alt.Y("cumulative_revenue_per_customer:Q", title="cumulative revenue / customer ($)"),
            color=alt.Color("cohort_month:N", title="cohort"),
            tooltip=["cohort_month", "months_since", "cumulative_revenue_per_customer"],
        )
        .properties(height=360),
        width="stretch",
    )

    st.subheader("Retention triangle")
    st.altair_chart(
        alt.Chart(retention)
        .mark_rect()
        .encode(
            x=alt.X("months_since:O", title="months since acquisition"),
            y=alt.Y("cohort_month:O", title="cohort"),
            color=alt.Color("retention_pct:Q", title="% active",
                            scale=alt.Scale(scheme="teals")),
            tooltip=["cohort_month", "months_since", "retention_pct", "active_customers"],
        )
        .properties(height=340),
        width="stretch",
    )

    st.subheader("Repeat behaviour by acquiring channel")
    st.dataframe(load("repeat_rate_by_channel"), width="stretch", hide_index=True)

    st.subheader("Channel efficiency (last-touch — the default, and the wrong one)")
    st.dataframe(
        eff[["channel", "channel_group", "spend", "sessions", "last_touch_conversions",
             "revenue", "cpa_last_touch", "roas_last_touch"]],
        width="stretch", hide_index=True,
    )

# ------------------------------------------------------------------- tab 4
with tab4:
    did = readout[readout["estimator"].str.startswith("difference")].iloc[0]
    naive = readout[readout["estimator"].str.startswith("naive")].iloc[0]
    p = power.iloc[0]
    true_lift = C.CHANNELS[C.EXPERIMENT_CHANNEL]["true_lift"]

    st.subheader(f"Geo holdout on {C.EXPERIMENT_CHANNEL}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Planted truth", f"{100 * true_lift:.2f}%")
    c2.metric("Difference-in-differences", f"{100 * did['estimated_lift']:.2f}%",
              f"95% CI {100 * did['ci_low']:.2f}% – {100 * did['ci_high']:.2f}%",
              delta_color="off")
    c3.metric("Naive pre/post", f"{100 * naive['estimated_lift']:.2f}%",
              "overstates the effect", delta_color="inverse")

    if bool(did["covers_truth"]):
        st.success(
            "The 95% interval contains the planted truth. The naive estimate does not — "
            "it reads a seasonal downswing that hit both arms as if the channel caused it.",
            icon=":material/task_alt:",
        )

    st.markdown("#### Power, computed before the readout")
    st.write(
        f"With **{p['sessions_treated_arm']:,}** sessions in the treated arm and a "
        f"baseline conversion rate of **{p['baseline_cvr']:.4f}**, this test could "
        f"detect a relative lift of **{100 * p['mde_relative']:.2f}%** at 80% power."
    )
    st.dataframe(
        pd.DataFrame(
            [
                {"channel": "paid_search", "true_lift": C.CHANNELS["paid_search"]["true_lift"],
                 "sessions_needed_per_arm": int(p["required_n_paid_search"]),
                 "testable at this traffic": int(p["required_n_paid_search"])
                 <= int(p["sessions_treated_arm"])},
                {"channel": "paid_social", "true_lift": C.CHANNELS["paid_social"]["true_lift"],
                 "sessions_needed_per_arm": int(p["required_n_paid_social"]),
                 "testable at this traffic": int(p["required_n_paid_social"])
                 <= int(p["sessions_treated_arm"])},
            ]
        ),
        width="stretch", hide_index=True,
    )
    st.caption(
        "Choosing a channel your traffic can actually power is the first decision in "
        "an incrementality test, and the one most often skipped. paid_social moves the "
        "conversion rate by 8 basis points — real, and out of reach here."
    )

st.divider()
st.caption(
    "All data synthetic (seed "
    f"{C.SEED}). Ground truth is computed by counterfactual re-simulation, not assumed. "
    "Source: github.com/KushPatel29/marketing-attribution-analytics"
)
