"""
SaaS unit economics — and the point where act two collides with act one.

CAC payback, the magic number and LTV:CAC are the three numbers a board asks
about, and all three have marketing spend in the denominator. Which means the
attribution model chosen in act one is not a marketing-team methodology
argument: it silently decides which channel looks efficient enough to fund.

So the channel CAC table below is computed *once per attribution model*. Same
spend, same bookings, six different answers about where the money should go.

    python saas/gtm_metrics.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402
from attribution.evaluate import MODELS  # noqa: E402


def load() -> dict[str, pd.DataFrame]:
    return {
        "opps": pd.read_csv(C.DATA / "saas_fact_opportunity.csv"),
        "cost": pd.read_csv(C.DATA / "saas_fact_sm_cost.csv"),
        "moves": pd.read_csv(C.DATA / "saas_fact_arr_movement.csv"),
        "retention": pd.read_csv(C.OUT / "retention_summary.csv"),
        "attribution": pd.read_csv(C.OUT / "attribution_comparison.csv"),
    }


def blended_economics(d: dict) -> pd.DataFrame:
    """CAC, payback and LTV:CAC for the business as a whole and by segment."""
    won = d["opps"][d["opps"]["is_won"] == 1]
    churn = d["retention"].set_index("segment")
    reps = pd.read_csv(C.DATA / "saas_dim_rep.csv")
    leads = pd.read_csv(C.DATA / "saas_fact_lead.csv")
    accounts = pd.read_csv(C.DATA / "saas_dim_account.csv")

    # Allocation basis matters, and the obvious one is circular: splitting cost
    # by each segment's share of ARR makes CAC proportional to deal size, which
    # forces every segment to the *same* payback period by construction. Sales
    # cost follows headcount and marketing cost follows lead volume instead --
    # neither of which is downstream of the answer.
    sales_total = float(d["cost"]["sales_cost"].sum())
    mkt_total = float(d["cost"]["marketing_cost"].sum())
    rep_share = reps.groupby("segment").size() / len(reps)
    lead_seg = leads.merge(accounts[["account_id", "segment"]], on="account_id")
    lead_share = lead_seg.groupby("segment").size() / len(lead_seg)

    rows = []
    for segment, g in won.groupby("segment"):
        sm = sales_total * float(rep_share[segment]) + mkt_total * float(lead_share[segment])
        customers = len(g)
        arr_per_customer = g["amount_arr"].mean()
        cac = sm / customers
        gross_profit_per_month = arr_per_customer * C.GROSS_MARGIN_SAAS / 12.0
        payback_months = cac / gross_profit_per_month
        # Annual dollar churn from the retention table -> expected lifetime.
        dollar_churn = float(churn.loc[segment, "dollar_churn_pct"]) / 100.0
        lifetime_years = 1.0 / dollar_churn if dollar_churn > 0 else float("inf")
        ltv = arr_per_customer * C.GROSS_MARGIN_SAAS * lifetime_years
        rows.append(
            {
                "segment": segment,
                "new_customers": customers,
                "new_arr": round(g["amount_arr"].sum(), 2),
                "allocated_sm_cost": round(sm, 2),
                "cac": round(cac, 2),
                "arr_per_customer": round(arr_per_customer, 2),
                "cac_payback_months": round(payback_months, 1),
                "annual_dollar_churn_pct": round(100 * dollar_churn, 2),
                "ltv": round(ltv, 2),
                "ltv_to_cac": round(ltv / cac, 2),
                # The two rules of thumb, so the verdict is not just a number.
                "payback_verdict": "healthy" if payback_months <= 12 else "slow",
                "ltv_cac_verdict": "healthy" if ltv / cac >= 3 else "thin",
            }
        )
    return pd.DataFrame(rows).sort_values("cac_payback_months", ignore_index=True)


def magic_number(d: dict) -> pd.DataFrame:
    """
    Net new ARR added per dollar of prior-period sales & marketing.

    Quarterly, and lagged by one quarter, because spend takes a quarter to
    show up as revenue. Above 0.75 the usual reading is "keep spending".
    """
    moves = d["moves"].copy()
    moves["quarter"] = pd.PeriodIndex(pd.to_datetime(moves["movement_month"]), freq="Q")
    cost = d["cost"].copy()
    cost["quarter"] = pd.PeriodIndex(pd.to_datetime(cost["month"]), freq="Q")

    net = moves.groupby("quarter")["arr_delta"].sum().rename("net_new_arr")
    spend = cost.groupby("quarter")["total_sm_cost"].sum().rename("sm_cost")
    df = pd.concat([net, spend], axis=1).sort_index()
    df["prior_quarter_sm_cost"] = df["sm_cost"].shift(1)
    # No x4. The textbook formula annualises a *quarterly revenue delta*; net
    # new ARR is already an annual number, so multiplying again inflates the
    # ratio roughly fourfold -- which is how a mediocre business ends up
    # reporting a magic number of 8.
    df["magic_number"] = df["net_new_arr"] / df["prior_quarter_sm_cost"]
    df = df.dropna().reset_index()
    df["quarter"] = df["quarter"].astype(str)
    df["verdict"] = df["magic_number"].apply(
        lambda m: "spend more" if m >= 0.75 else ("hold" if m >= 0.5 else "fix efficiency")
    )
    for c in ("net_new_arr", "sm_cost", "prior_quarter_sm_cost", "magic_number"):
        df[c] = df[c].round(2)
    return df


def cac_by_attribution_model(d: dict) -> pd.DataFrame:
    """
    The collision. Marketing spend is split across channels by whichever
    attribution model you believe; new ARR is split by which channel sourced
    the lead. Divide one by the other and every model hands you a different
    ranking of which channel is cheapest.
    """
    won = d["opps"][d["opps"]["is_won"] == 1]
    arr_by_channel = won.groupby("source_channel")["amount_arr"].sum()
    customers_by_channel = won.groupby("source_channel").size()
    marketing_spend = d["cost"]["marketing_cost"].sum()
    shares = d["attribution"].set_index("channel")
    # Only paid channels. Allocating budget to organic or direct and calling
    # the result a cost per acquisition invents a price for something nobody
    # bought, and it is exactly where "direct is our cheapest channel" comes
    # from. Shares are renormalised across the paid set.
    paid = [c for c, m in C.CHANNELS.items() if m["is_paid"]]

    rows = []
    for model in [*MODELS, "true_incremental_share"]:
        col = shares.loc[shares.index.isin(paid), model]
        col = col / col.sum()
        for channel in arr_by_channel.index:
            if channel not in col.index:
                continue
            spend = marketing_spend * float(col[channel])
            arr = float(arr_by_channel[channel])
            n = int(customers_by_channel[channel])
            rows.append(
                {
                    "model": "TRUTH" if model == "true_incremental_share" else model,
                    "channel": channel,
                    "allocated_marketing_spend": round(spend, 2),
                    "new_arr": round(arr, 2),
                    "customers": n,
                    "cac": round(spend / n, 2),
                    "arr_per_marketing_dollar": round(arr / spend, 2) if spend else None,
                }
            )
    return pd.DataFrame(rows)


def cheapest_channel_by_model(cac: pd.DataFrame) -> pd.DataFrame:
    """One row per model: which channel it says is cheapest, and does it agree with truth?"""
    best = cac.loc[cac.groupby("model")["cac"].idxmin()].reset_index(drop=True)
    truth_channel = best.loc[best["model"] == "TRUTH", "channel"].iloc[0]
    best["agrees_with_truth"] = best["channel"] == truth_channel
    return best[["model", "channel", "cac", "arr_per_marketing_dollar", "agrees_with_truth"]]


def main() -> None:
    d = load()
    econ = blended_economics(d)
    magic = magic_number(d)
    cac = cac_by_attribution_model(d)
    cheapest = cheapest_channel_by_model(cac)

    C.OUT.mkdir(parents=True, exist_ok=True)
    econ.to_csv(C.OUT / "saas_unit_economics.csv", index=False, lineterminator="\n")
    magic.to_csv(C.OUT / "saas_magic_number.csv", index=False, lineterminator="\n")
    cac.to_csv(C.OUT / "saas_cac_by_model.csv", index=False, lineterminator="\n")
    cheapest.to_csv(C.OUT / "saas_cheapest_channel_by_model.csv", index=False,
                    lineterminator="\n")

    pd.set_option("display.width", 170)
    print("Unit economics by segment\n")
    print(econ[["segment", "new_customers", "cac", "cac_payback_months",
                "ltv", "ltv_to_cac", "payback_verdict", "ltv_cac_verdict"]].to_string(index=False))

    print("\n\nMagic number by quarter\n")
    print(magic[["quarter", "net_new_arr", "prior_quarter_sm_cost",
                 "magic_number", "verdict"]].to_string(index=False))

    print("\n\nWhich channel looks cheapest, per attribution model\n")
    print(cheapest.to_string(index=False))
    disagree = (~cheapest.loc[cheapest["model"] != "TRUTH", "agrees_with_truth"]).sum()
    total = len(cheapest) - 1
    print(
        f"\n  {disagree} of {total} attribution models name a different cheapest channel "
        f"than the planted truth does."
    )


if __name__ == "__main__":
    main()
