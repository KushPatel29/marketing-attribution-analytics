"""
Act two: a B2B SaaS go-to-market dataset, shaped like a CRM.

Act one asks "which channel caused the sale?" of an e-commerce funnel where a
conversion is one session and the money arrives once. Almost nothing about a
SaaS motion works that way, and the metrics that GTM teams actually run on
exist because of the difference:

  * Revenue recurs, so the interesting number is not what a customer paid but
    what the *book* of customers did — new, expansion, contraction, churn.
    That is why this writes an ARR movement ledger rather than an order table.
  * A conversion is a months-long opportunity moving through stages, owned by
    a rep against a quota. That is why opportunities carry a stage history:
    without it you cannot compute stage conversion or sales-cycle length, and
    those two are most of pipeline diagnosis.

Field names follow Salesforce conventions (`is_closed`, `is_won`, `stage_name`,
`amount_arr`, `loss_reason`) so the SQL reads the way it would against a real
org rather than against a shape invented for this repo.

    python saas/generate_gtm_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402


def _write(df: pd.DataFrame, name: str) -> None:
    C.DATA.mkdir(parents=True, exist_ok=True)
    df.to_csv(C.DATA / name, index=False, lineterminator="\n")
    print(f"  {name:<32} {len(df):>7,} rows")


def month_floor(ts: pd.Timestamp) -> str:
    return ts.to_period("M").start_time.strftime("%Y-%m-%d")


# --------------------------------------------------------------- dimensions
def build_dims(rng: np.random.Generator) -> tuple[pd.DataFrame, ...]:
    stages = pd.DataFrame(
        [
            {"stage_id": i + 1, "stage_name": n, "stage_order": o,
             "is_closed": int(c), "is_won": int(w)}
            for i, (n, o, c, w) in enumerate(C.SAAS_STAGES)
        ]
    )

    territories = pd.DataFrame(
        {
            "territory_id": np.arange(1, C.N_TERRITORIES + 1),
            "territory_name": [f"T{i:02d}" for i in range(1, C.N_TERRITORIES + 1)],
            "region": rng.choice(C.REGIONS, size=C.N_TERRITORIES),
        }
    )

    # One rep per (territory, segment) so quota attainment has a real
    # denominator and capacity can be compared against coverage.
    reps = []
    rid = 0
    start = pd.Timestamp(C.SAAS_START)
    for t in territories.itertuples():
        for seg in C.SEGMENTS:
            rid += 1
            # Some reps are hired mid-period, which is what makes ramp matter.
            hire_offset = int(rng.choice([0, 0, 0, 3, 7, 11, 15], p=[.4, .12, .1, .1, .1, .1, .08]))
            reps.append(
                {
                    "rep_id": rid,
                    "rep_name": f"Rep {rid:03d}",
                    "territory_id": t.territory_id,
                    "segment": seg,
                    "hire_month": month_floor(start + pd.DateOffset(months=hire_offset)),
                    "ramp_months": C.REP_RAMP_MONTHS,
                    "annual_quota": C.QUOTA_PER_REP[seg],
                }
            )
    reps = pd.DataFrame(reps)

    segs = list(C.SEGMENTS)
    seg_p = np.array([C.SEGMENTS[s]["share"] for s in segs])
    accounts = pd.DataFrame(
        {
            "account_id": np.arange(1, C.N_ACCOUNTS + 1),
            "account_name": [f"Account {i:04d}" for i in range(1, C.N_ACCOUNTS + 1)],
            "segment": rng.choice(segs, size=C.N_ACCOUNTS, p=seg_p / seg_p.sum()),
            "industry": rng.choice(C.INDUSTRIES, size=C.N_ACCOUNTS),
            "employees": rng.integers(12, 9000, size=C.N_ACCOUNTS),
        }
    )
    accounts["territory_id"] = rng.integers(1, C.N_TERRITORIES + 1, size=C.N_ACCOUNTS)
    return stages, territories, reps, accounts


# -------------------------------------------------------------------- leads
def build_leads(accounts: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """
    Leads carry the acquisition channel, which is the join back to act one:
    the same seven channels, so CAC can be re-cut by any attribution model.
    """
    start = pd.Timestamp(C.SAAS_START)
    horizon = C.SAAS_MONTHS * 30
    rows = []
    # Weight creation toward later months: a growing company acquires more
    # leads each month, and a flat rate would leave nothing in flight at the
    # end of the window.
    day_w = np.array([(1 + C.LEAD_GROWTH_PER_MONTH) ** (d / 30.0) for d in range(horizon)])
    day_w = day_w / day_w.sum()
    for a in accounts.itertuples():
        created = start + pd.Timedelta(days=int(rng.choice(horizon, p=day_w)))
        # Self-serve arrivals are the PLG motion; the rest are marketing-sourced.
        plg = rng.random() < C.PLG_SIGNUP_RATE
        channel = rng.choice(C.CHANNEL_NAMES)
        mql = rng.random() < 0.58
        sql = mql and rng.random() < 0.47
        rows.append(
            {
                "lead_id": a.account_id,
                "account_id": a.account_id,
                "created_date": created.strftime("%Y-%m-%d"),
                "created_month": month_floor(created),
                "source_channel": channel,
                "is_self_serve": int(plg),
                "is_mql": int(mql),
                "mql_date": (created + pd.Timedelta(days=int(rng.integers(1, 21))))
                .strftime("%Y-%m-%d") if mql else "",
                "is_sql": int(sql),
                "sql_date": (created + pd.Timedelta(days=int(rng.integers(21, 60))))
                .strftime("%Y-%m-%d") if sql else "",
            }
        )
    return pd.DataFrame(rows)


# ------------------------------------------------------------ opportunities
def build_opportunities(
    accounts: pd.DataFrame, leads: pd.DataFrame, reps: pd.DataFrame,
    rng: np.random.Generator, pql_account_ids: set[int] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Opportunities plus their stage history.

    An opportunity that loses does not skip to Closed Lost from wherever it
    started — it dies at a specific stage, and *which* stage is the whole
    diagnosis. Deals dying at Demo mean a qualification problem; deals dying
    at Negotiation mean a pricing problem. So each opportunity walks the
    ladder and stops where it stops.
    """
    start = pd.Timestamp(C.SAAS_START)
    end = start + pd.DateOffset(months=C.SAAS_MONTHS)
    open_stages = [s for s in C.SAAS_STAGES if not s[2]]

    rep_pool = {
        (t, s): g["rep_id"].tolist()
        for (t, s), g in reps.groupby(["territory_id", "segment"])
    }
    lead_ch = leads.set_index("account_id")["source_channel"]
    lead_created = pd.to_datetime(leads.set_index("account_id")["created_date"])
    pql_accounts = set(pql_account_ids or [])

    opps, hist = [], []
    oid = 0
    for a in accounts.itertuples():
        meta = C.SEGMENTS[a.segment]
        # SMB reps run many small fast deals; Enterprise reps run few large slow
        # ones. That ratio is most of why the two motions cannot share a
        # dashboard, and it drives both pipeline volume and coverage.
        n_opps = max(1, int(rng.poisson(C.OPPS_PER_ACCOUNT[a.segment])))
        for k in range(n_opps):
            oid += 1
            created = lead_created[a.account_id] + pd.Timedelta(
                days=int(rng.integers(14, 70)) + k * int(rng.integers(200, 380))
            )
            if created >= end:
                continue
            cycle = max(int(rng.normal(meta["cycle_days"], meta["cycle_days"] * 0.32)), 7)
            wr = meta["win_rate"]
            if a.account_id in pql_accounts:
                wr = min(wr * C.PQL_WIN_RATE_MULTIPLIER, 0.95)
            won = rng.random() < wr
            # Where an open deal has got to; closed deals ran the full ladder.
            still_open = created + pd.Timedelta(days=cycle) > end
            arr = float(np.round(rng.lognormal(np.log(meta["arr_mean"]), 0.42), -2))

            if still_open:
                reached = int(rng.integers(1, len(open_stages) + 1))
                final_stage = open_stages[reached - 1][0]
                is_closed = is_won = 0
                close_date = created + pd.Timedelta(days=cycle)
            else:
                reached = len(open_stages) if won else int(rng.integers(2, len(open_stages) + 1))
                final_stage = "Closed Won" if won else "Closed Lost"
                is_closed, is_won = 1, int(won)
                close_date = created + pd.Timedelta(days=cycle)

            reps_here = rep_pool.get((a.territory_id, a.segment)) or reps["rep_id"].tolist()
            rep_id = int(rng.choice(reps_here))

            opps.append(
                {
                    "opportunity_id": oid,
                    "account_id": a.account_id,
                    "rep_id": rep_id,
                    "segment": a.segment,
                    "territory_id": a.territory_id,
                    "source_channel": lead_ch[a.account_id],
                    "created_date": created.strftime("%Y-%m-%d"),
                    "created_month": month_floor(created),
                    "close_date": close_date.strftime("%Y-%m-%d"),
                    "close_month": month_floor(close_date),
                    "stage_name": final_stage,
                    "is_closed": is_closed,
                    "is_won": is_won,
                    "amount_arr": arr,
                    "sales_cycle_days": int(cycle) if is_closed else None,
                    "loss_reason": str(rng.choice(C.LOSS_REASONS))
                    if (is_closed and not is_won) else "",
                    "competitor": str(rng.choice(C.COMPETITORS))
                    if (is_closed and not is_won) else "",
                }
            )

            # Stage history: split the elapsed time across the stages reached.
            walked = open_stages[:reached]
            weights = rng.dirichlet(np.ones(len(walked)))
            cursor = created
            for (name, order, _, _), w in zip(walked, weights, strict=True):
                dwell = max(int(cycle * w), 1)
                hist.append(
                    {
                        "opportunity_id": oid,
                        "stage_name": name,
                        "stage_order": order,
                        "entered_date": cursor.strftime("%Y-%m-%d"),
                        "exited_date": (cursor + pd.Timedelta(days=dwell)).strftime("%Y-%m-%d"),
                        "days_in_stage": dwell,
                    }
                )
                cursor += pd.Timedelta(days=dwell)
            if is_closed:
                hist.append(
                    {
                        "opportunity_id": oid,
                        "stage_name": final_stage,
                        "stage_order": 6 if is_won else 7,
                        "entered_date": close_date.strftime("%Y-%m-%d"),
                        "exited_date": "",
                        "days_in_stage": 0,
                    }
                )

    return pd.DataFrame(opps), pd.DataFrame(hist)


# ------------------------------------------------- subscriptions & ARR moves
def build_arr(opps: pd.DataFrame, rng: np.random.Generator) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Turn won deals into subscriptions, then walk each anniversary and record
    what happened as an ARR movement.

    The movement ledger is the point. Every SaaS revenue metric — NRR, GRR,
    logo churn versus dollar churn, the whole waterfall — is an aggregation
    of these five movement types, and none of them can be derived from a
    balance alone.
    """
    start = pd.Timestamp(C.SAAS_START)
    end = start + pd.DateOffset(months=C.SAAS_MONTHS)
    won = opps[opps["is_won"] == 1]

    subs, moves = [], []

    # An existing book of business at t0. Without it, net revenue retention is
    # computed over whichever handful of subscriptions happened to reach an
    # anniversary inside a 24-month window — a real company opens the period
    # already carrying customers, and NRR is a statement about *them*.
    # These predate the window, so their original bookings are deliberately
    # NOT recorded as `new`: they would inflate new-business ARR with revenue
    # that was won before the period being reported.
    legacy_segments = list(C.SEGMENTS)
    legacy_p = np.array([C.SEGMENTS[s]["share"] for s in legacy_segments])
    for i in range(C.N_LEGACY_SUBSCRIPTIONS):
        seg = str(rng.choice(legacy_segments, p=legacy_p / legacy_p.sum()))
        rules = C.RENEWAL[seg]
        # Started 1-3 years before the window, so anniversaries land inside it.
        begin = start - pd.DateOffset(months=int(rng.integers(12, 37)))
        arr = float(np.round(rng.lognormal(np.log(C.SEGMENTS[seg]["arr_mean"]), 0.42), -2))
        sub_id = f"SUB-LEGACY-{i + 1:04d}"
        account_id = -(i + 1)  # negative ids keep the legacy book identifiable
        subs.append({
            "subscription_id": sub_id, "account_id": account_id, "segment": seg,
            "start_date": begin.strftime("%Y-%m-%d"), "start_month": month_floor(begin),
            "initial_arr": arr,
        })
        anniversary = begin + pd.DateOffset(years=1)
        while anniversary < end and arr > 0:
            if anniversary >= start:
                roll = rng.random()
                if roll < rules["churn"]:
                    moves.append({
                        "movement_month": month_floor(anniversary), "account_id": account_id,
                        "subscription_id": sub_id, "segment": seg,
                        "movement_type": "churn", "arr_delta": -arr, "arr_after": 0.0,
                    })
                    arr = 0.0
                    break
                if roll < rules["churn"] + rules["contract"]:
                    delta = -round(arr * float(rng.uniform(0.10, 0.30)), 2)
                    arr = round(arr + delta, 2)
                    mtype = "contraction"
                elif roll < rules["churn"] + rules["contract"] + rules["expand"]:
                    delta = max(round(arr * float(rng.normal(rules["expand_pct"], 0.06)), 2), 0.0)
                    arr = round(arr + delta, 2)
                    mtype = "expansion"
                else:
                    delta, mtype = 0.0, "flat"
                if mtype != "flat":
                    moves.append({
                        "movement_month": month_floor(anniversary), "account_id": account_id,
                        "subscription_id": sub_id, "segment": seg,
                        "movement_type": mtype, "arr_delta": delta, "arr_after": arr,
                    })
            anniversary += pd.DateOffset(years=1)

    for o in won.itertuples():
        rules = C.RENEWAL[o.segment]
        begin = pd.Timestamp(o.close_date)
        arr = o.amount_arr
        sub_id = f"SUB-{o.opportunity_id:05d}"
        subs.append(
            {
                "subscription_id": sub_id,
                "account_id": o.account_id,
                "segment": o.segment,
                "start_date": begin.strftime("%Y-%m-%d"),
                "start_month": month_floor(begin),
                "initial_arr": arr,
            }
        )
        moves.append(
            {
                "movement_month": month_floor(begin), "account_id": o.account_id,
                "subscription_id": sub_id, "segment": o.segment,
                "movement_type": "new", "arr_delta": arr, "arr_after": arr,
            }
        )

        anniversary = begin + pd.DateOffset(years=1)
        while anniversary < end and arr > 0:
            roll = rng.random()
            if roll < rules["churn"]:
                moves.append({
                    "movement_month": month_floor(anniversary), "account_id": o.account_id,
                    "subscription_id": sub_id, "segment": o.segment,
                    "movement_type": "churn", "arr_delta": -arr, "arr_after": 0.0,
                })
                arr = 0.0
                break
            if roll < rules["churn"] + rules["contract"]:
                delta = -round(arr * float(rng.uniform(0.10, 0.30)), 2)
                arr = round(arr + delta, 2)
                mtype = "contraction"
            elif roll < rules["churn"] + rules["contract"] + rules["expand"]:
                delta = round(arr * float(rng.normal(rules["expand_pct"], 0.06)), 2)
                delta = max(delta, 0.0)
                arr = round(arr + delta, 2)
                mtype = "expansion"
            else:
                delta, mtype = 0.0, "flat"
            if mtype != "flat":
                moves.append({
                    "movement_month": month_floor(anniversary), "account_id": o.account_id,
                    "subscription_id": sub_id, "segment": o.segment,
                    "movement_type": mtype, "arr_delta": delta, "arr_after": arr,
                })
            anniversary += pd.DateOffset(years=1)

    return pd.DataFrame(subs), pd.DataFrame(moves)


# ---------------------------------------------------------------- PLG funnel
def build_plg(
    accounts: pd.DataFrame, leads: pd.DataFrame, rng: np.random.Generator
) -> pd.DataFrame:
    """Self-serve signup -> activation -> product-qualified, with time-to-value."""
    self_serve = leads[leads["is_self_serve"] == 1]
    seg = accounts.set_index("account_id")["segment"]
    rows = []
    for lead in self_serve.itertuples():
        signup = pd.Timestamp(lead.created_date)
        activated = rng.random() < C.PLG_ACTIVATION_RATE
        ttv = int(rng.gamma(2.0, 3.2)) + 1 if activated else None
        pql = activated and rng.random() < C.PLG_PQL_RATE
        rows.append(
            {
                "account_id": lead.account_id,
                "segment": seg[lead.account_id],
                "source_channel": lead.source_channel,
                "signup_date": signup.strftime("%Y-%m-%d"),
                "signup_month": month_floor(signup),
                "is_activated": int(activated),
                "activation_date": (signup + pd.Timedelta(days=ttv))
                .strftime("%Y-%m-%d") if activated else "",
                "days_to_value": ttv if activated else None,
                "is_pql": int(pql),
                "pql_date": (signup + pd.Timedelta(days=ttv + int(rng.integers(3, 30))))
                .strftime("%Y-%m-%d") if pql else "",
            }
        )
    return pd.DataFrame(rows)


def build_costs(reps: pd.DataFrame) -> pd.DataFrame:
    """Monthly S&M cost, the denominator for CAC payback and the magic number."""
    start = pd.Timestamp(C.SAAS_START)
    rows = []
    for m in range(C.SAAS_MONTHS):
        month = month_floor(start + pd.DateOffset(months=m))
        active = (reps["hire_month"] <= month).sum()
        # Marketing spend scales with the demand it is generating. Holding it
        # flat while lead volume compounds would make the business look
        # miraculously more efficient every quarter for no reason.
        mkt = C.MARKETING_COST_PER_MONTH * (1 + C.LEAD_GROWTH_PER_MONTH) ** m
        sales = active * C.SALES_COST_PER_REP_MONTH
        rows.append(
            {
                "month": month,
                "reps_active": int(active),
                "sales_cost": round(sales, 2),
                "marketing_cost": round(mkt, 2),
                "total_sm_cost": round(sales + mkt, 2),
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    rng = np.random.default_rng(C.SAAS_SEED)
    print(f"Generating {C.N_ACCOUNTS:,} SaaS accounts over {C.SAAS_MONTHS} months "
          f"(seed {C.SAAS_SEED})")

    stages, territories, reps, accounts = build_dims(rng)
    leads = build_leads(accounts, rng)
    # PLG first: which accounts became product-qualified is an input to the
    # opportunity model, not a consequence of it.
    plg = build_plg(accounts, leads, rng)
    pql_ids = set(plg.loc[plg["is_pql"] == 1, "account_id"].tolist())
    opps, hist = build_opportunities(accounts, leads, reps, rng, pql_ids)
    subs, moves = build_arr(opps, rng)
    costs = build_costs(reps)

    print("\nWriting data/")
    _write(stages, "saas_dim_stage.csv")
    _write(territories, "saas_dim_territory.csv")
    _write(reps, "saas_dim_rep.csv")
    _write(accounts, "saas_dim_account.csv")
    _write(leads, "saas_fact_lead.csv")
    _write(opps, "saas_fact_opportunity.csv")
    _write(hist, "saas_fact_stage_history.csv")
    _write(subs, "saas_fact_subscription.csv")
    _write(moves, "saas_fact_arr_movement.csv")
    _write(plg, "saas_fact_plg.csv")
    _write(costs, "saas_fact_sm_cost.csv")

    closed = opps[opps["is_closed"] == 1]
    print(
        f"\n  {len(opps):,} opportunities, {len(closed):,} closed, "
        f"win rate {closed['is_won'].mean():.1%}, "
        f"${opps.loc[opps['is_won'] == 1, 'amount_arr'].sum():,.0f} new ARR booked"
    )
    net = moves.groupby("movement_type")["arr_delta"].sum()
    for k in ("new", "expansion", "contraction", "churn"):
        if k in net:
            print(f"    {k:<12} ${net[k]:>12,.0f}")


if __name__ == "__main__":
    main()
