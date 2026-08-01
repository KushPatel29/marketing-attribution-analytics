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
    pipeline = load("pipeline_funnel")
    coverage = load("pipeline_coverage")
    winrate = load("win_rate_by_segment")
    velocity = load("stage_velocity")
    waterfall = load("arr_waterfall")
    retention_summary = load("retention_summary")
    econ = load("saas_unit_economics")
    magic = load("saas_magic_number")
    cheapest = load("saas_cheapest_channel_by_model")
    plg = load("plg_funnel")
    plg_motion = load("plg_vs_sales_led")
except FileNotFoundError:
    st.error(
        "No outputs found. Run the pipeline first:\n\n"
        "```\npython data_generator/generate_marketing_data.py\n"
        "python engine/run_analytics.py\n"
        "python attribution/evaluate.py\n"
        "python experiments/incrementality.py\n```"
    )
    st.stop()

# Deliberately a radio rather than st.tabs. Charts and dataframes inside a
# hidden tab are measured while their container has zero width, so every
# section except the one active on first load rendered as an empty box. A radio
# puts only the selected section in the DOM, so everything measures correctly.
SECTIONS = [
    "🎯 Attribution vs truth",
    "🔻 Funnel",
    "👥 Cohorts & LTV",
    "🧪 The experiment",
    "💼 SaaS pipeline & retention",
    "💰 Unit economics",
]
section = st.radio("Section", SECTIONS, horizontal=True, label_visibility="collapsed")
st.divider()

# --------------------------------------------------------- section: attribution
if section == SECTIONS[0]:
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

# --------------------------------------------------------- section: funnel
if section == SECTIONS[1]:
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

# --------------------------------------------------------- section: cohorts
if section == SECTIONS[2]:
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

# --------------------------------------------------------- section: experiment
if section == SECTIONS[3]:
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
        f"With **{int(p['sessions_treated_arm']):,}** sessions in the treated arm and a "
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

# --------------------------------------------------- section: SaaS pipeline
if section == SECTIONS[4]:
    st.subheader("Act two — a B2B SaaS go-to-market motion")
    st.caption(
        "CRM-shaped: accounts, opportunities walking a stage ladder, an ARR "
        "movement ledger. These metrics exist because revenue recurs and a "
        "conversion takes months — neither of which is true in act one."
    )

    smb = coverage[coverage["segment"] == "SMB"].iloc[0]
    ent = coverage[coverage["segment"] == "Enterprise"].iloc[0]
    c1, c2, c3 = st.columns(3)
    c1.metric("SMB coverage", f"{smb['coverage_ratio']:.2f}x",
              f"needs {smb['required_coverage']:.2f}x", delta_color="off")
    c2.metric("Enterprise coverage", f"{ent['coverage_ratio']:.2f}x",
              f"needs {ent['required_coverage']:.2f}x", delta_color="off")
    # Escaped: Streamlit renders an unescaped $...$ pair as inline LaTeX.
    c3.metric("Ending ARR", f"\\${waterfall['ending_arr'].iloc[-1]:,.0f}")

    st.info(
        "**The 3x rule gets SMB backwards.** Required coverage falls out of a "
        "segment's own win rate and cycle length: SMB needs 1.19x and has 1.60x, "
        "so it is fine — while the blended heuristic calls it short. Enterprise "
        "needs more than nine.",
        icon=":material/rule:",
    )
    st.dataframe(
        coverage[["segment", "win_rate_pct", "avg_cycle_days", "coverage_ratio",
                  "required_coverage", "coverage_verdict", "verdict_under_3x_rule"]],
        width="stretch", hide_index=True,
    )

    st.subheader("Pipeline funnel")
    pf = pipeline.sort_values("stage_order")
    st.altair_chart(
        alt.Chart(pf).mark_bar(color=NAVY).encode(
            y=alt.Y("stage_name:N", sort=pf["stage_name"].tolist(), title=None),
            x=alt.X("opportunities:Q", title="opportunities reaching stage"),
            tooltip=["stage_name", "opportunities", "stage_conversion_pct"],
        ).properties(height=250),
        width="stretch",
    )

    left, right = st.columns(2)
    with left:
        st.subheader("Win rate and cycle by segment")
        st.dataframe(
            winrate[["segment", "win_rate_pct", "avg_cycle_won", "won_arr"]],
            width="stretch", hide_index=True,
        )
    with right:
        st.subheader("Slowest stages")
        st.caption("The Enterprise bottleneck is getting in the room, not closing.")
        st.dataframe(
            velocity.sort_values("avg_days_in_stage", ascending=False)
            .head(6)[["segment", "stage_name", "avg_days_in_stage"]],
            width="stretch", hide_index=True,
        )

    st.subheader("ARR waterfall")
    long_w = waterfall.melt(
        id_vars=["movement_month"],
        value_vars=["new_arr", "expansion_arr", "contraction_arr", "churn_arr"],
        var_name="movement", value_name="arr",
    )
    st.altair_chart(
        alt.Chart(long_w).mark_bar().encode(
            x=alt.X("movement_month:T", title=None),
            y=alt.Y("arr:Q", title="ARR movement ($)", stack="zero"),
            color=alt.Color("movement:N", title=None,
                            scale=alt.Scale(range=[NAVY, TEAL, "#E8A33D", ORANGE])),
            tooltip=["movement_month", "movement", "arr"],
        ).properties(height=320),
        width="stretch",
    )

    st.subheader("Net vs gross revenue retention")
    st.caption(
        "NRR counts expansion; GRR does not. Enterprise clears 100% net while "
        "losing 16% gross — quoting only NRR answers a question nobody asked."
    )
    st.dataframe(
        retention_summary[["segment", "nrr_pct", "grr_pct", "logo_churn_pct",
                           "dollar_churn_pct"]],
        width="stretch", hide_index=True,
    )

    st.subheader("Product-led funnel")
    a_col, b_col = st.columns([2, 3])
    with a_col:
        st.dataframe(plg[["step", "accounts", "step_conversion_pct"]],
                     width="stretch", hide_index=True)
    with b_col:
        st.dataframe(
            plg_motion[["motion", "opportunities", "win_rate_pct", "avg_cycle_days"]],
            width="stretch", hide_index=True,
        )
        st.caption(
            "Product-qualified accounts win far better — but they self-select, so "
            "the gap overstates the causal effect. Nobody randomised anything here."
        )

# ------------------------------------------------- section: unit economics
if section == SECTIONS[5]:
    st.subheader("Does the motion pay for itself?")
    worst_seg = econ.sort_values("ltv_to_cac").iloc[0]
    best_seg = econ.sort_values("ltv_to_cac").iloc[-1]

    c1, c2, c3 = st.columns(3)
    c1.metric(f"{best_seg['segment']} LTV:CAC", f"{best_seg['ltv_to_cac']:.2f}",
              f"payback {best_seg['cac_payback_months']:.1f} mo", delta_color="off")
    c2.metric(f"{worst_seg['segment']} LTV:CAC", f"{worst_seg['ltv_to_cac']:.2f}",
              f"payback {worst_seg['cac_payback_months']:.1f} mo", delta_color="inverse")
    c3.metric("Magic number (latest quarter)", f"{magic['magic_number'].iloc[-1]:.2f}")

    st.warning(
        f"**{worst_seg['segment']} has the fastest cycle, the highest win rate and "
        f"the healthiest coverage in the business — and an LTV:CAC of "
        f"{worst_seg['ltv_to_cac']:.2f}.** Every operational metric says it is the "
        f"best-run segment. The unit economics say it should probably not exist.",
        icon=":material/warning:",
    )
    st.dataframe(
        econ[["segment", "new_customers", "cac", "cac_payback_months",
              "annual_dollar_churn_pct", "ltv", "ltv_to_cac", "ltv_cac_verdict"]],
        width="stretch", hide_index=True,
    )

    st.subheader("Where act one and act two collide")
    st.caption(
        "Marketing spend sits in the denominator of every number above, so the "
        "attribution model you believe decides which channel looks fundable. "
        "Paid channels only — you cannot have a cost per acquisition for a "
        "channel nobody bought."
    )
    st.dataframe(
        cheapest[["model", "channel", "cac", "arr_per_marketing_dollar",
                  "agrees_with_truth"]],
        width="stretch", hide_index=True,
    )
    disagree = int((~cheapest.loc[cheapest["model"] != "TRUTH", "agrees_with_truth"]).sum())
    st.caption(
        f"{disagree} of 6 models name a different cheapest channel than the truth. "
        "The one that agrees is last-touch — the worst model in act one — and it "
        "gets there by undercrediting display so hard that almost no budget is "
        "allocated to it. A right answer from a broken mechanism."
    )

    st.subheader("Magic number by quarter")
    st.altair_chart(
        alt.Chart(magic).mark_bar(color=TEAL).encode(
            x=alt.X("quarter:N", title=None),
            y=alt.Y("magic_number:Q", title="net new ARR per $ of prior-quarter S&M"),
            tooltip=["quarter", "net_new_arr", "prior_quarter_sm_cost",
                     "magic_number", "verdict"],
        ).properties(height=280),
        width="stretch",
    )
    st.caption(
        "Deliberately not multiplied by four: the textbook formula annualises a "
        "quarterly revenue delta, and net new ARR is already annual."
    )

st.divider()
st.caption(
    "All data synthetic (seeds "
    f"{C.SEED} and {C.SAAS_SEED}). Ground truth is computed by counterfactual "
    "re-simulation, not assumed. "
    "Source: github.com/KushPatel29/marketing-attribution-analytics"
)
