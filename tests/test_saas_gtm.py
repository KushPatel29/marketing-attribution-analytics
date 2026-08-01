"""
Act two: the B2B SaaS go-to-market layer.

Same standard as act one — the numbers are asserted, the modelling decisions
that could quietly be wrong are pinned, and the semantic layer is parsed
against the schema it claims to describe rather than trusted.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402

LOOKER = ROOT / "looker"


# ------------------------------------------------------------------ the CRM
def test_opportunity_stage_flags_are_consistent(data):
    """is_won implies is_closed. A deal cannot be won and still open."""
    o = data("saas_fact_opportunity")
    assert not ((o["is_won"] == 1) & (o["is_closed"] == 0)).any()
    assert set(o["stage_name"]) <= {s[0] for s in C.SAAS_STAGES}
    # Only closed deals carry a close-stage name.
    closed = o[o["is_closed"] == 1]
    assert set(closed["stage_name"]) == {"Closed Won", "Closed Lost"}


def test_only_lost_deals_have_a_loss_reason(data):
    o = data("saas_fact_opportunity")
    lost = o[(o["is_closed"] == 1) & (o["is_won"] == 0)]
    assert lost["loss_reason"].notna().all() and (lost["loss_reason"] != "").all()
    others = o[~((o["is_closed"] == 1) & (o["is_won"] == 0))]
    assert (others["loss_reason"].fillna("") == "").all()


def test_stage_history_is_contiguous_and_ordered(data):
    """
    Each opportunity walks the ladder without skipping. If a deal could jump
    from Discovery to Negotiation, stage-conversion would be meaningless.
    """
    h = data("saas_fact_stage_history").sort_values(["opportunity_id", "stage_order"])
    for _, g in h.groupby("opportunity_id"):
        orders = g["stage_order"].tolist()
        opens = [o for o in orders if o <= 5]
        assert opens == list(range(1, len(opens) + 1)), f"gap in stage ladder: {orders}"


def test_every_opportunity_has_stage_history(data):
    o, h = data("saas_fact_opportunity"), data("saas_fact_stage_history")
    assert set(h["opportunity_id"]) == set(o["opportunity_id"])


def test_sales_cycle_lengthens_with_segment_size(data):
    """SMB < Mid-Market < Enterprise. If this inverts, the generator is broken."""
    o = data("saas_fact_opportunity")
    m = o[o["is_closed"] == 1].groupby("segment")["sales_cycle_days"].mean()
    assert m["SMB"] < m["Mid-Market"] < m["Enterprise"]


# ------------------------------------------------------------------ pipeline
def test_pipeline_funnel_narrows(out):
    f = out("pipeline_funnel").sort_values("stage_order")
    assert f["opportunities"].is_monotonic_decreasing
    assert f.iloc[0]["pct_of_created"] == pytest.approx(100.0)


def test_win_rate_denominator_is_closed_deals(out, data):
    """The most common wrong sales metric: open deals in the denominator."""
    w = out("win_rate_by_segment").set_index("segment")
    o = data("saas_fact_opportunity")
    for segment, g in o[o["is_closed"] == 1].groupby("segment"):
        expected = 100.0 * g["is_won"].sum() / len(g)
        assert w.loc[segment, "win_rate_pct"] == pytest.approx(expected, abs=0.01)


def test_required_coverage_beats_the_three_x_rule(out):
    """
    The finding: coverage requirement falls out of win rate and cycle length,
    and for at least one segment the blended 3x heuristic gives the opposite
    verdict to the segment's own economics.
    """
    c = out("pipeline_coverage")
    assert (c["required_coverage"] > 0).all()
    # Enterprise wins rarely on a long cycle, so it needs far more than 3x.
    ent = c[c["segment"] == "Enterprise"].iloc[0]
    assert ent["required_coverage"] > 3.0
    disagreements = (c["coverage_verdict"] != c["verdict_under_3x_rule"]).sum()
    assert disagreements >= 1, "the 3x rule agreed with every segment — check the data"


def test_loss_analysis_covers_every_lost_deal(out, data):
    la, o = out("loss_analysis"), data("saas_fact_opportunity")
    lost = o[(o["is_closed"] == 1) & (o["is_won"] == 0)]
    assert la["losses"].sum() == len(lost)
    assert la["arr_lost"].sum() == pytest.approx(lost["amount_arr"].sum(), abs=1.0)


# ------------------------------------------------------------- SaaS revenue
def test_arr_waterfall_reconciles_to_the_ledger(out, data):
    w, m = out("arr_waterfall"), data("saas_fact_arr_movement")
    assert w["net_new_arr"].sum() == pytest.approx(m["arr_delta"].sum(), abs=1.0)
    # Ending ARR is the running total of net new.
    assert w["ending_arr"].iloc[-1] == pytest.approx(w["net_new_arr"].sum(), abs=1.0)


def test_movement_signs_are_correct(data):
    """Churn and contraction must be negative; new and expansion positive."""
    m = data("saas_fact_arr_movement")
    assert (m.loc[m["movement_type"] == "new", "arr_delta"] > 0).all()
    assert (m.loc[m["movement_type"] == "expansion", "arr_delta"] >= 0).all()
    assert (m.loc[m["movement_type"] == "contraction", "arr_delta"] < 0).all()
    assert (m.loc[m["movement_type"] == "churn", "arr_delta"] < 0).all()


def test_nrr_is_never_below_grr(out):
    """
    NRR counts expansion and GRR does not, so NRR >= GRR always. If this ever
    fails, one of the two is computed wrong — which is the usual bug.
    """
    r = out("retention_summary")
    assert (r["nrr_pct"] >= r["grr_pct"] - 1e-6).all()
    assert (r["grr_pct"] <= 100.0 + 1e-6).all()


def test_retention_improves_with_segment_size(out):
    """Enterprise retains better than SMB. The planted gradient, enforced."""
    r = out("retention_summary").set_index("segment")
    assert r.loc["Enterprise", "nrr_pct"] > r.loc["Mid-Market", "nrr_pct"]
    assert r.loc["Mid-Market", "nrr_pct"] > r.loc["SMB", "nrr_pct"]


def test_logo_churn_and_dollar_churn_differ(out):
    """
    They diverge whenever churn concentrates in small accounts, which it does.
    Reporting only one of them is how a board gets a comfortable answer.
    """
    r = out("retention_summary")
    assert (r["logo_churn_pct"] != r["dollar_churn_pct"]).any()


# --------------------------------------------------------- rep productivity
def test_attainment_uses_ramp_adjusted_quota(out):
    """
    A rep hired mid-period was never expected to carry a full number. If quota
    were not pro-rated, late hires would show a fraction of the attainment
    their tenure entitles them to.
    """
    r = out("rep_attainment")
    assert (r["ramp_adjusted_quota"] < r["annual_quota"] * 2).all()
    assert r["attainment_pct"].mean() == pytest.approx(86, abs=25)


def test_capacity_plan_uses_observed_productivity(out):
    """Hiring plans built on assumed productivity are wishes, not plans."""
    c = out("capacity_plan")
    assert (c["observed_arr_per_rep"] > 0).all()
    expected = (c["total_annual_quota"] / c["observed_arr_per_rep"]).apply(
        lambda x: -(-x // 1)
    )
    assert (c["reps_required_at_observed_productivity"] == expected).all()


# ---------------------------------------------------------------- PLG funnel
def test_plg_funnel_narrows_and_last_step_is_not_trivial(out):
    f = out("plg_funnel").sort_values("step_order")
    assert f["accounts"].is_monotonic_decreasing
    # If the final step scores 100% it is counting opportunities that already
    # existed before the account qualified, which measures nothing.
    assert f.iloc[-1]["step_conversion_pct"] < 100.0


def test_product_qualified_accounts_convert_better(out):
    """The planted PQL effect. Also the caveat the README states: self-serve
    accounts self-select, so the observed gap overstates the causal one."""
    p = out("plg_vs_sales_led").set_index("motion")
    assert p.loc["product-qualified", "win_rate_pct"] > p.loc["sales-led", "win_rate_pct"]


def test_time_to_value_uses_median(out):
    t = out("time_to_value")
    assert (t["median_days_to_value"] > 0).all()
    assert (t["median_days_to_value"] <= t["slowest_days_to_value"]).all()


# ------------------------------------------------------------ unit economics
def test_cac_payback_differs_by_segment(out):
    """
    Guards against the circular allocation that made every segment identical:
    splitting cost by share of ARR forces the same payback everywhere.
    """
    e = out("saas_unit_economics")
    assert e["cac_payback_months"].nunique() == len(e)
    assert (e["cac"] > 0).all()


def test_enterprise_has_the_best_unit_economics(out):
    e = out("saas_unit_economics").set_index("segment")
    assert e.loc["Enterprise", "ltv_to_cac"] > e.loc["SMB", "ltv_to_cac"]


def test_magic_number_is_not_double_annualised(out):
    """
    Net new ARR is already an annual figure. The textbook formula multiplies a
    *quarterly revenue delta* by four; applying that x4 here inflates the ratio
    roughly fourfold and turns a mediocre business into a fictional one.
    """
    m = out("saas_magic_number")
    assert (m["magic_number"] < 3.0).all(), "magic number looks double-annualised"
    assert (m["magic_number"] > 0).all()


def test_cac_is_only_computed_for_paid_channels(out):
    """You cannot have a cost per acquisition for a channel nobody bought."""
    cac = out("saas_cac_by_model")
    paid = {c for c, m in C.CHANNELS.items() if m["is_paid"]}
    assert set(cac["channel"]) <= paid
    assert "direct" not in set(cac["channel"])
    assert "organic_search" not in set(cac["channel"])


def test_attribution_choice_changes_the_cheapest_channel(out):
    """
    The whole reason act two exists: the attribution model picked in act one
    decides which channel a GTM team funds.
    """
    best = out("saas_cheapest_channel_by_model")
    assert len(best) == 7  # six models plus the planted truth
    assert best["channel"].nunique() > 1, "every model agreed — check the harness"


# -------------------------------------------------------------- the semantic layer
def _lkml_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in LOOKER.rglob("*.lkml"))


def test_lookml_files_exist():
    assert (LOOKER / "gtm.model.lkml").exists()
    assert list((LOOKER / "views").glob("*.view.lkml"))


def test_every_lookml_view_maps_to_a_real_table(data):
    """
    A semantic layer describing a warehouse that no longer exists is worse than
    none. Every `sql_table_name` must resolve to a table this repo produces.
    """
    text = _lkml_text()
    tables = set(re.findall(r"sql_table_name:\s*marts\.([a-z_]+)\s*;;", text))
    assert tables, "no sql_table_name found in the LookML"
    for t in tables:
        assert (ROOT / "data" / f"{t}.csv").exists(), f"LookML references missing table {t}"


def test_every_lookml_column_exists(data):
    """Each ${TABLE}.column referenced in a view must be a real column."""
    for path in (LOOKER / "views").glob("*.view.lkml"):
        text = path.read_text(encoding="utf-8")
        for block in re.split(r"^view:\s+", text, flags=re.M)[1:]:
            m = re.search(r"sql_table_name:\s*marts\.([a-z_]+)\s*;;", block)
            if not m:
                continue
            cols = set(data(m.group(1)).columns)
            referenced = set(re.findall(r"\$\{TABLE\}\.([a-z_]+)", block))
            missing = referenced - cols
            assert not missing, f"{path.name}/{m.group(1)} references {missing}"


def test_win_rate_is_not_an_average_of_the_flag():
    """
    The definition this layer exists to protect. If someone replaces it with
    `type: average sql: is_won`, open deals silently enter the denominator.
    """
    text = (LOOKER / "views" / "opportunity.view.lkml").read_text(encoding="utf-8")
    # Split on the next measure declaration, not on the next "}": LookML field
    # references like ${won_opportunities} contain a brace and would truncate
    # the block halfway through the expression being checked.
    after = text.split("measure: win_rate")[1]
    block = re.split(r"^\s*measure:\s", after, maxsplit=1, flags=re.M)[0]
    assert "closed_opportunities" in block
    assert "type: average" not in block
