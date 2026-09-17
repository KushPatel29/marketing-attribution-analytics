"""Decision-room controls: method selection, reconciliation and allocation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize(
    "name",
    [
        "measurement_strategy",
        "outcome_reconciliation",
        "budget_response_curves",
        "budget_allocation",
        "budget_portfolio_summary",
    ],
)
def test_decision_csv_is_committed(name):
    assert (ROOT / "output" / f"{name}.csv").exists()


def test_every_decision_has_one_measurement_method(out):
    strategy = out("measurement_strategy")
    assert strategy["decision"].is_unique
    assert strategy["recommended_method"].notna().all()
    assert strategy["next_action"].notna().all()


def test_mmm_is_explicitly_not_ready(out):
    strategy = out("measurement_strategy")
    mmm = strategy[strategy["recommended_method"].str.contains("MMM")].iloc[0]
    assert mmm["current_readiness"] == "NOT READY"
    assert "2–3 years" in mmm["minimum_evidence"]


def test_attribution_is_never_labelled_causal(out):
    strategy = out("measurement_strategy")
    attribution = strategy[
        strategy["recommended_method"] == "Multi-touch attribution"
    ].iloc[0]
    assert attribution["causal_strength"] == "Descriptive only"
    assert "never label credit as incrementality" in attribution["next_action"]


def test_only_paid_channels_enter_the_allocation(out):
    allocation = out("budget_allocation")
    assert set(allocation["channel"]) == {
        "paid_search",
        "paid_social",
        "display",
        "affiliate",
    }


def test_reconciliation_keeps_three_conversion_concepts(out):
    reconciliation = out("outcome_reconciliation")
    required = {
        "platform_reported_conversions",
        "modeled_attributed_conversions",
        "true_incremental_conversions",
    }
    assert required <= set(reconciliation.columns)
    assert not reconciliation[list(required)].isna().any().any()


def test_reconciliation_does_not_force_attribution_to_equal_incrementality(out):
    reconciliation = out("outcome_reconciliation")
    assert (
        reconciliation["modeled_attributed_conversions"]
        - reconciliation["true_incremental_conversions"]
    ).abs().max() > 25


@pytest.mark.parametrize("channel", ["paid_social", "display", "affiliate"])
def test_uncalibrated_channels_are_planning_only(out, channel):
    reconciliation = out("outcome_reconciliation").set_index("channel")
    assert reconciliation.loc[channel, "decision_use"] == "Planning prior only"
    assert "prior" in reconciliation.loc[channel, "incrementality_evidence"].lower()


def test_paid_search_is_the_only_experiment_calibrated_channel(out):
    reconciliation = out("outcome_reconciliation")
    calibrated = reconciliation[
        reconciliation["incrementality_evidence"].str.startswith("Experiment")
    ]
    assert calibrated["channel"].tolist() == ["paid_search"]


@pytest.mark.parametrize("channel", ["paid_search", "paid_social", "display", "affiliate"])
def test_response_curve_is_monotonic_and_saturating(out, channel):
    curve = out("budget_response_curves")
    values = curve[curve["channel"] == channel].sort_values("scenario_spend")
    assert values["expected_incremental_orders"].is_monotonic_increasing
    gains = values["expected_incremental_orders"].diff().dropna()
    assert gains.iloc[-1] < gains.iloc[0]


@pytest.mark.parametrize("channel", ["paid_search", "paid_social", "display", "affiliate"])
def test_response_interval_contains_the_expected_case(out, channel):
    curve = out("budget_response_curves")
    c = curve[curve["channel"] == channel]
    assert (c["downside_incremental_orders"] <= c["expected_incremental_orders"]).all()
    assert (c["expected_incremental_orders"] <= c["upside_incremental_orders"]).all()


def test_budget_is_held_constant_to_the_cent(out):
    allocation = out("budget_allocation")
    assert allocation["recommended_spend"].sum() == pytest.approx(
        allocation["current_spend"].sum(), abs=0.01
    )


@pytest.mark.parametrize("channel", ["paid_search", "paid_social", "display", "affiliate"])
def test_every_channel_respects_its_floor_and_cap(out, channel):
    row = out("budget_allocation").set_index("channel").loc[channel]
    ratio = 100 * row["recommended_spend"] / row["current_spend"]
    assert ratio >= row["floor_pct"] - 0.01
    assert ratio <= row["cap_pct"] + 0.01


def test_optimizer_improves_the_centre_case(out):
    allocation = out("budget_allocation")
    assert allocation["expected_incremental_orders"].sum() > (
        allocation["current_incremental_orders"].sum()
    )


def test_optimizer_does_not_turn_modelled_uplift_into_realized_uplift(out):
    summary = out("budget_portfolio_summary").iloc[0]
    assert summary["recommendation"] == "CONDITIONAL APPROVAL"
    assert "test-gated" in summary["release_gate"]
    assert "Re-read" in summary["realization_check"]


def test_uncalibrated_changes_remain_test_gated(out):
    allocation = out("budget_allocation")
    uncalibrated = allocation[~allocation["evidence"].str.startswith("Experiment")]
    changed = uncalibrated[uncalibrated["spend_change_pct"].abs() > 5]
    assert not changed.empty
    assert set(changed["decision_status"]) == {"TEST BEFORE SCALE"}


def test_every_channel_has_an_authority_and_next_action(out):
    allocation = out("budget_allocation")
    assert allocation["approval_authority"].str.len().min() > 5
    assert allocation["required_next_action"].str.len().min() > 10


def test_summary_reconciles_to_allocation(out):
    allocation = out("budget_allocation")
    summary = out("budget_portfolio_summary").iloc[0]
    assert summary["current_budget"] == pytest.approx(allocation["current_spend"].sum())
    assert summary["recommended_budget"] == pytest.approx(
        allocation["recommended_spend"].sum()
    )
    assert summary["expected_incremental_orders"] == pytest.approx(
        allocation["expected_incremental_orders"].sum(), abs=0.01
    )


def test_decision_packet_integrity_hash_is_reproducible():
    packet = json.loads(
        (ROOT / "output" / "marketing_investment_decision.json").read_text(encoding="utf-8")
    )
    digest = packet.pop("integrity")
    canonical = json.dumps(packet, sort_keys=True, separators=(",", ":"))
    actual = hashlib.sha256(canonical.encode()).hexdigest()
    assert digest["algorithm"] == "SHA-256"
    assert actual == digest["canonical_payload_sha256"]


def test_decision_packet_states_the_demonstration_boundary():
    packet = json.loads(
        (ROOT / "output" / "marketing_investment_decision.json").read_text(encoding="utf-8")
    )
    boundary = packet["demonstration_boundary"]
    assert "Synthetic" in boundary
    assert "planning priors" in boundary
    assert "must not be represented as observed causal effects" in boundary


def test_executive_memo_is_decision_complete():
    memo = (ROOT / "output" / "marketing_investment_memo.md").read_text(encoding="utf-8")
    for phrase in (
        "Recommendation:",
        "Budget:",
        "Evidence boundary",
        "Release conditions",
        "Audit evidence",
    ):
        assert phrase in memo


def test_decision_outputs_have_no_missing_values(out):
    for name in (
        "measurement_strategy",
        "outcome_reconciliation",
        "budget_response_curves",
        "budget_allocation",
        "budget_portfolio_summary",
    ):
        assert not out(name).isna().any().any(), name
