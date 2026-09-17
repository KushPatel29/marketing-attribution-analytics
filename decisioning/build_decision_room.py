"""Build the governed measurement and budget-allocation evidence pack.

This stage deliberately does not fit a media-mix model. The dataset contains
only twelve months for one synthetic advertiser, which is not enough history
to identify adstock, seasonality and channel response without pretending. It
instead turns the truths already established by the simulator and geo test
into a decision workflow:

* select the measurement method that matches the decision;
* reconcile platform, attribution and incremental outcomes without blending
  their meanings;
* allocate the existing paid budget across transparent saturation curves;
* optimize against a downside-weighted value under explicit business limits;
* emit a signed decision packet that can be reviewed independently of the UI.

Every amount is synthetic. Only paid_search has experiment-calibrated evidence;
the other response curves are planning priors that remain gated for testing.

    python decisioning/build_decision_room.py
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config as C  # noqa: E402

PAID_CHANNELS = ("paid_search", "paid_social", "display", "affiliate")
POLICY = {
    "paid_search": {
        "floor": 0.75,
        "cap": 1.25,
        "confidence": "Experiment calibrated",
        "authority": "Growth VP + Experimentation Lead",
        "action": "Scale within guardrail; repeat geo holdout quarterly",
    },
    "paid_social": {
        "floor": 0.70,
        "cap": 1.35,
        "confidence": "Planning prior — underpowered",
        "authority": "Growth VP after power review",
        "action": "Hold near baseline; pool geos or extend test duration",
    },
    "display": {
        "floor": 0.60,
        "cap": 1.50,
        "confidence": "Planning prior — test required",
        "authority": "Growth VP after lift study approval",
        "action": "Run lift test before treating modeled upside as realized",
    },
    "affiliate": {
        "floor": 0.70,
        "cap": 1.30,
        "confidence": "Planning prior — test required",
        "authority": "Channel Owner + Finance Partner",
        "action": "Validate partner-level incrementality before scaling",
    },
}

# Wider bands are attached to evidence that has not been calibrated by a test.
PRIOR_BANDS = {
    "paid_social": (0.50, 1.50),
    "display": (0.45, 1.55),
    "affiliate": (0.55, 1.45),
}


def _money(value: float) -> str:
    return f"${value:,.0f}"


def measurement_strategy() -> pd.DataFrame:
    """The method-selection contract used by the decision-room intake."""
    rows = [
        {
            "decision": "Reconcile platform delivery and journey reporting",
            "question": "Which touchpoints were present before conversion?",
            "recommended_method": "Multi-touch attribution",
            "causal_strength": "Descriptive only",
            "minimum_evidence": (
                "Identity rules, deduplication, conversion lag, spend reconciliation"
            ),
            "current_readiness": "READY",
            "next_action": "Use for journey diagnosis; never label credit as incrementality",
        },
        {
            "decision": "Approve a material change to one scalable channel",
            "question": "Would conversions change if the channel were reduced or removed?",
            "recommended_method": "Randomized geo holdout or lift study",
            "causal_strength": "Causal when design assumptions hold",
            "minimum_evidence": "Pre-period fit, enough randomized units, power, no spillover",
            "current_readiness": "READY — paid_search",
            "next_action": "Use the geo result and interval; re-power every other channel",
        },
        {
            "decision": "Set the cross-channel annual planning envelope",
            "question": "What are lagged, saturated channel contributions over time?",
            "recommended_method": "MMM calibrated to experiments",
            "causal_strength": "Model-based; calibration required",
            "minimum_evidence": (
                "2–3 years weekly data, controls, adstock, saturation, holdout validation"
            ),
            "current_readiness": "NOT READY",
            "next_action": "Collect longer history; do not fit MMM to the current 12-month window",
        },
        {
            "decision": "Tune creative, audience or placement within a platform",
            "question": "Which treatment performs better inside the channel?",
            "recommended_method": "Randomized A/B or conversion-lift test",
            "causal_strength": "Causal within tested population",
            "minimum_evidence": (
                "Stable assignment, pre-registered outcome, MDE and guardrail metrics"
            ),
            "current_readiness": "DESIGN REQUIRED",
            "next_action": "Write the decision and MDE before inspecting treatment results",
        },
    ]
    return pd.DataFrame(rows)


def _band_by_channel(readout: pd.DataFrame) -> dict[str, tuple[float, float]]:
    did = readout[readout["estimator"].str.startswith("difference")].iloc[0]
    estimate = float(did["estimated_lift"])
    if estimate <= 0:
        search_band = (0.0, 2.0)
    else:
        search_band = (
            max(0.0, float(did["ci_low"]) / estimate),
            max(1.0, float(did["ci_high"]) / estimate),
        )
    return {"paid_search": search_band, **PRIOR_BANDS}


def build_reconciliation(
    efficiency: pd.DataFrame,
    comparison: pd.DataFrame,
    truth: pd.DataFrame,
) -> pd.DataFrame:
    """Keep delivery, attribution credit and incremental outcomes separate."""
    paid = efficiency[efficiency["is_paid"] == 1].copy()
    total_conversions = float(efficiency["last_touch_conversions"].sum())
    model = comparison[["channel", "position_based"]].copy()
    model["modeled_attributed_conversions"] = model["position_based"] * total_conversions
    out = paid.merge(model[["channel", "modeled_attributed_conversions"]], on="channel")
    out = out.merge(
        truth[["channel", "true_incremental_conversions"]], on="channel", how="left"
    )
    out = out.rename(columns={"last_touch_conversions": "platform_reported_conversions"})
    out["attribution_minus_incremental"] = (
        out["modeled_attributed_conversions"] - out["true_incremental_conversions"]
    )
    out["reporting_status"] = out["attribution_minus_incremental"].map(
        lambda x: "OVER-CREDITED" if x > 25 else ("UNDER-CREDITED" if x < -25 else "ALIGNED")
    )
    out["incrementality_evidence"] = out["channel"].map(
        lambda ch: POLICY[ch]["confidence"]
    )
    out["decision_use"] = out["channel"].map(
        lambda ch: "Investment calibration" if ch == "paid_search" else "Planning prior only"
    )
    cols = [
        "channel",
        "spend",
        "platform_reported_conversions",
        "modeled_attributed_conversions",
        "true_incremental_conversions",
        "attribution_minus_incremental",
        "reporting_status",
        "incrementality_evidence",
        "decision_use",
    ]
    return out[cols].sort_values("spend", ascending=False).reset_index(drop=True)


def _curve_parameters(current_spend: float, current_orders: float) -> tuple[float, float]:
    """Return a transparent saturating curve that passes through today's point."""
    ceiling = current_orders * 2.4
    scale = -current_spend / math.log(1.0 - current_orders / ceiling)
    return ceiling, scale


def _orders(spend: float, ceiling: float, scale: float) -> float:
    return ceiling * (1.0 - math.exp(-max(0.0, spend) / scale))


def build_response_curves(
    reconciliation: pd.DataFrame,
    readout: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, dict[str, float]]]:
    bands = _band_by_channel(readout)
    rows: list[dict] = []
    parameters: dict[str, dict[str, float]] = {}
    for row in reconciliation.itertuples(index=False):
        current_spend = float(row.spend)
        current_orders = float(row.true_incremental_conversions)
        ceiling, scale = _curve_parameters(current_spend, current_orders)
        low_multiplier, high_multiplier = bands[row.channel]
        parameters[row.channel] = {
            "current_spend": current_spend,
            "ceiling": ceiling,
            "scale": scale,
            "low_multiplier": low_multiplier,
            "high_multiplier": high_multiplier,
        }
        for pct in range(50, 151, 5):
            scenario_spend = current_spend * pct / 100
            expected = _orders(scenario_spend, ceiling, scale)
            rows.append(
                {
                    "channel": row.channel,
                    "spend_pct_of_current": pct,
                    "scenario_spend": round(scenario_spend, 2),
                    "expected_incremental_orders": round(expected, 3),
                    "downside_incremental_orders": round(expected * low_multiplier, 3),
                    "upside_incremental_orders": round(expected * high_multiplier, 3),
                    "evidence": POLICY[row.channel]["confidence"],
                }
            )
    return pd.DataFrame(rows), parameters


def _risk_adjusted_orders(spend: float, params: dict[str, float]) -> float:
    expected = _orders(spend, params["ceiling"], params["scale"])
    downside = expected * params["low_multiplier"]
    # The optimizer is deliberately conservative: most weight sits on the
    # lower bound, not the attractive centre estimate.
    return 0.35 * expected + 0.65 * downside


def constrained_allocation(
    parameters: dict[str, dict[str, float]],
    step: float = 25.0,
) -> dict[str, float]:
    """Greedy concave allocator with floors, caps and an unchanged total budget."""
    total = sum(p["current_spend"] for p in parameters.values())
    allocation = {
        ch: p["current_spend"] * POLICY[ch]["floor"] for ch, p in parameters.items()
    }
    caps = {ch: p["current_spend"] * POLICY[ch]["cap"] for ch, p in parameters.items()}
    remaining = total - sum(allocation.values())
    while remaining > 0.005:
        increment = min(step, remaining)
        candidates = []
        for channel, current in allocation.items():
            room = caps[channel] - current
            if room <= 0.005:
                continue
            delta = min(increment, room)
            gain = _risk_adjusted_orders(current + delta, parameters[channel]) - (
                _risk_adjusted_orders(current, parameters[channel])
            )
            candidates.append((gain / delta, channel, delta))
        if not candidates:
            raise RuntimeError("Allocation constraints cannot absorb the total budget")
        _, channel, delta = max(candidates)
        allocation[channel] += delta
        remaining -= delta
    return allocation


def build_allocation(
    reconciliation: pd.DataFrame,
    parameters: dict[str, dict[str, float]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    proposed = constrained_allocation(parameters)
    rows = []
    for channel in PAID_CHANNELS:
        p = parameters[channel]
        current = p["current_spend"]
        rec = proposed[channel]
        current_orders = _orders(current, p["ceiling"], p["scale"])
        expected = _orders(rec, p["ceiling"], p["scale"])
        downside = expected * p["low_multiplier"]
        change_pct = 100 * (rec / current - 1.0)
        if POLICY[channel]["confidence"].startswith("Experiment"):
            status = "CONDITIONALLY APPROVE"
        elif abs(change_pct) <= 5:
            status = "HOLD / MEASURE"
        else:
            status = "TEST BEFORE SCALE"
        rows.append(
            {
                "channel": channel,
                "current_spend": round(current, 2),
                "recommended_spend": round(rec, 2),
                "spend_change": round(rec - current, 2),
                "spend_change_pct": round(change_pct, 2),
                "current_incremental_orders": round(current_orders, 3),
                "expected_incremental_orders": round(expected, 3),
                "downside_incremental_orders": round(downside, 3),
                "evidence": POLICY[channel]["confidence"],
                "decision_status": status,
                "approval_authority": POLICY[channel]["authority"],
                "required_next_action": POLICY[channel]["action"],
                "floor_pct": int(POLICY[channel]["floor"] * 100),
                "cap_pct": int(POLICY[channel]["cap"] * 100),
            }
        )
    allocation = pd.DataFrame(rows)
    current_total = float(allocation["current_spend"].sum())
    proposed_total = float(allocation["recommended_spend"].sum())
    current_orders = float(allocation["current_incremental_orders"].sum())
    expected_orders = float(allocation["expected_incremental_orders"].sum())
    downside_orders = float(allocation["downside_incremental_orders"].sum())
    summary = pd.DataFrame(
        [
            {
                "decision_id": "MKT-ALLOC-2026-001",
                "as_of_date": date(2026, 9, 17).isoformat(),
                "decision_owner": "Growth VP",
                "measurement_owner": "Experimentation Lead",
                "finance_reviewer": "Finance Business Partner",
                "current_budget": round(current_total, 2),
                "recommended_budget": round(proposed_total, 2),
                "budget_variance": round(proposed_total - current_total, 2),
                "current_incremental_orders": round(current_orders, 3),
                "expected_incremental_orders": round(expected_orders, 3),
                "expected_order_uplift_pct": round(100 * (expected_orders / current_orders - 1), 2),
                "downside_incremental_orders": round(downside_orders, 3),
                "recommendation": "CONDITIONAL APPROVAL",
                "release_gate": "1 calibrated channel; 3 channels remain test-gated",
                "realization_check": "Re-read after 8 weeks; compare realized lift with interval",
            }
        ]
    )
    return allocation, summary


def write_decision_packet(
    strategy: pd.DataFrame,
    reconciliation: pd.DataFrame,
    allocation: pd.DataFrame,
    summary: pd.DataFrame,
) -> None:
    record = summary.iloc[0].to_dict()
    payload = {
        "decision": record,
        "guardrails": allocation[
            [
                "channel",
                "current_spend",
                "recommended_spend",
                "floor_pct",
                "cap_pct",
                "evidence",
                "decision_status",
                "approval_authority",
                "required_next_action",
            ]
        ].to_dict(orient="records"),
        "measurement_readiness": strategy[
            ["recommended_method", "current_readiness", "next_action"]
        ].to_dict(orient="records"),
        "reconciliation": reconciliation[
            [
                "channel",
                "platform_reported_conversions",
                "modeled_attributed_conversions",
                "true_incremental_conversions",
                "reporting_status",
            ]
        ].round(3).to_dict(orient="records"),
        "demonstration_boundary": (
            "Synthetic decision-support demonstration. paid_search is calibrated to the "
            "synthetic geo experiment; all other channel curves are planning priors and "
            "must not be represented as observed causal effects."
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    packet = {
        **payload,
        "integrity": {
            "algorithm": "SHA-256",
            "canonical_payload_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
        },
    }
    (C.OUT / "marketing_investment_decision.json").write_text(
        json.dumps(packet, indent=2) + "\n", encoding="utf-8"
    )

    memo = f"""# Marketing investment decision — {record['decision_id']}

**Recommendation:** {record['recommendation']}

**Budget:** {_money(record['current_budget'])} current → \
{_money(record['recommended_budget'])} recommended

**Expected incremental orders:** {record['current_incremental_orders']:.1f} → \
{record['expected_incremental_orders']:.1f} \
({record['expected_order_uplift_pct']:.1f}% modelled uplift)

## Decision

Keep total paid media spend unchanged, apply the channel floors and caps in the
decision packet, and treat every increase outside paid search as **test-gated**.
The allocation is optimized against a downside-weighted response, not the centre
forecast alone. It is a planning recommendation, not a realized outcome.

## Evidence boundary

Paid search is calibrated to the synthetic geo holdout. The other paid-channel
response curves are synthetic planning priors with wider uncertainty. The current
12-month history is intentionally not used to fit an MMM; the project requires
2–3 years of weekly data, controls, diagnostics and experiment calibration first.

## Release conditions

1. Growth VP owns the investment decision; Finance reviews budget neutrality.
2. Experimentation Lead validates power, pre-period fit and spillover risk.
3. Display, affiliate and paid-social changes do not become scale decisions until
   their required tests are complete.
4. Re-read after eight weeks and compare realized lift with the registered interval.

## Audit evidence

The machine-readable packet at `output/marketing_investment_decision.json` contains
the per-channel guardrails, named authorities, evidence class, required next action
and a SHA-256 digest over the canonical payload.
"""
    (C.OUT / "marketing_investment_memo.md").write_text(memo, encoding="utf-8")


def main() -> None:
    efficiency = pd.read_csv(C.OUT / "channel_efficiency.csv")
    comparison = pd.read_csv(C.OUT / "attribution_comparison.csv")
    truth = pd.read_csv(C.DATA / "ground_truth_incrementality.csv")
    readout = pd.read_csv(C.OUT / "incrementality_readout.csv")

    strategy = measurement_strategy()
    reconciliation = build_reconciliation(efficiency, comparison, truth)
    curves, parameters = build_response_curves(reconciliation, readout)
    allocation, summary = build_allocation(reconciliation, parameters)

    C.OUT.mkdir(parents=True, exist_ok=True)
    strategy.to_csv(C.OUT / "measurement_strategy.csv", index=False, lineterminator="\n")
    reconciliation.to_csv(
        C.OUT / "outcome_reconciliation.csv", index=False, lineterminator="\n"
    )
    curves.to_csv(C.OUT / "budget_response_curves.csv", index=False, lineterminator="\n")
    allocation.to_csv(C.OUT / "budget_allocation.csv", index=False, lineterminator="\n")
    summary.to_csv(C.OUT / "budget_portfolio_summary.csv", index=False, lineterminator="\n")
    write_decision_packet(strategy, reconciliation, allocation, summary)

    row = summary.iloc[0]
    print("Marketing investment decision room")
    print(f"  decision          : {row['decision_id']} — {row['recommendation']}")
    print(f"  budget            : {_money(row['current_budget'])} (held constant)")
    print(
        "  expected orders   : "
        f"{row['current_incremental_orders']:.1f} -> {row['expected_incremental_orders']:.1f} "
        f"({row['expected_order_uplift_pct']:.1f}%)"
    )
    print(f"  release gate      : {row['release_gate']}")
    print("  wrote 5 CSVs, a signed JSON decision packet and an executive memo")


if __name__ == "__main__":
    main()
