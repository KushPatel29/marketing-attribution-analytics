"""
Generate the synthetic marketing dataset — and, crucially, its ground truth.

The conversion model is a noisy-OR over the channels a user was actually
touched by:

    p(convert) = 1 - (1 - baseline) * PROD_c (1 - lift_c) ** min(touches_c, cap)

Each user is assigned one uniform draw `u`, and converts when `u < p`. Keeping
that draw fixed is what makes the ground truth exact: to ask "would this user
still have converted without email?", we recompute `p` with email's touches
removed and compare it to *the same* `u`. Users whose conversion survives the
removal were never email's to claim; users whose conversion disappears are
email's true incremental contribution.

This is the counterfactual a marketing team can never run on its live traffic,
which is precisely why attribution models exist and why nobody can grade them.
Here we can.

One consequence worth stating up front, because it shapes the whole analysis:
**incremental credit does not sum to the number of conversions.** A user touched
by both email and paid search may convert only because *both* happened, and
removing either one alone loses the conversion — so that single conversion is
counted as incremental for two different channels. Attribution models, which
must split each conversion into shares totalling 100%, are therefore answering
a subtly different question than the one the business cares about. Every model
in `attribution/` inherits an irreducible error from that gap, and the README
says so rather than hiding it behind a leaderboard.

    python data_generator/generate_marketing_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402


# --------------------------------------------------------------------- utils
def _write(df: pd.DataFrame, name: str) -> None:
    """CRLF on Windows would make CI's regenerated files differ byte-for-byte."""
    C.DATA.mkdir(parents=True, exist_ok=True)
    df.to_csv(C.DATA / name, index=False, lineterminator="\n")
    print(f"  {name:<34} {len(df):>7,} rows")


def _weights(key: str) -> np.ndarray:
    w = np.array([C.CHANNELS[c][key] for c in C.CHANNEL_NAMES], dtype=float)
    return w / w.sum()


def conversion_probability(counts: np.ndarray, lifts: np.ndarray) -> np.ndarray:
    """
    Noisy-OR over channels. `counts` is (n_users, n_channels) touch counts.

    Vectorised because the counterfactual loop calls this once per channel and
    once for the full journey, on every user.
    """
    capped = np.minimum(counts, C.MAX_EFFECTIVE_TOUCHES)
    survive = np.prod((1.0 - lifts) ** capped, axis=1)
    return 1.0 - (1.0 - C.BASELINE_CONVERSION) * survive


# ---------------------------------------------------------------- dimensions
def build_dim_channel() -> pd.DataFrame:
    rows = [
        {
            "channel_id": i + 1,
            "channel": name,
            "channel_group": meta["group"],
            "is_paid": int(meta["is_paid"]),
            "cost_per_click": meta["cpc"],
        }
        for i, (name, meta) in enumerate(C.CHANNELS.items())
    ]
    return pd.DataFrame(rows)


def build_dim_date(start: pd.Timestamp, days: int) -> pd.DataFrame:
    d = pd.date_range(start, periods=days, freq="D")
    return pd.DataFrame(
        {
            "date_key": d.strftime("%Y%m%d").astype(int),
            "date": d.strftime("%Y-%m-%d"),
            "year": d.year,
            "month": d.month,
            "month_start": d.to_period("M").start_time.strftime("%Y-%m-%d"),
            "week_start": (d - pd.to_timedelta(d.dayofweek, unit="D")).strftime("%Y-%m-%d"),
            "day_of_week": d.dayofweek,
            "is_weekend": (d.dayofweek >= 5).astype(int),
        }
    )


# ------------------------------------------------------------------ journeys
def build_journeys(rng: np.random.Generator) -> tuple[np.ndarray, list[list[int]]]:
    """
    One touch sequence per user. Returns the (n_users, n_channels) count matrix
    and the ordered channel-index sequences (order matters for first/last-touch).

    Sequence length is zero-truncated Poisson: most users see a couple of
    touches, a tail sees many. The first touch is drawn from `open_weight`, the
    last from `close_weight`, and the middle uniformly from the opening mix —
    which is how "direct" ends up closing journeys it did nothing to create.
    """
    n = C.N_USERS
    n_ch = len(C.CHANNEL_NAMES)
    open_p, close_p = _weights("open_weight"), _weights("close_weight")

    lengths = rng.poisson(2.6, size=n) + 1  # >= 1 touch
    lengths = np.minimum(lengths, 12)

    sequences: list[list[int]] = []
    counts = np.zeros((n, n_ch), dtype=np.int32)
    for i, L in enumerate(lengths):
        if L == 1:
            seq = [int(rng.choice(n_ch, p=close_p))]
        else:
            first = int(rng.choice(n_ch, p=open_p))
            last = int(rng.choice(n_ch, p=close_p))
            middle = list(rng.choice(n_ch, size=L - 2, p=open_p)) if L > 2 else []
            seq = [first, *[int(m) for m in middle], last]
        sequences.append(seq)
        for c in seq:
            counts[i, c] += 1
    return counts, sequences


def resolve_conversions(
    counts: np.ndarray, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    """
    Draw conversions, then measure each channel's true incremental contribution
    by re-running the same draw with that channel's touches removed.
    """
    lifts = np.array([C.CHANNELS[c]["true_lift"] for c in C.CHANNEL_NAMES])
    p_full = conversion_probability(counts, lifts)
    u = rng.random(len(counts))
    converted = u < p_full

    rows = []
    for j, name in enumerate(C.CHANNEL_NAMES):
        without = counts.copy()
        without[:, j] = 0
        p_without = conversion_probability(without, lifts)
        # Converted with the channel, would not have without it.
        pivotal = converted & (u >= p_without)
        touched = counts[:, j] > 0
        rows.append(
            {
                "channel": name,
                "true_lift": C.CHANNELS[name]["true_lift"],
                "users_touched": int(touched.sum()),
                "conversions_touched": int((converted & touched).sum()),
                "true_incremental_conversions": int(pivotal.sum()),
            }
        )

    truth = pd.DataFrame(rows)
    # Normalised so it can be compared against attribution models, which are
    # forced to split each conversion into shares summing to 1.
    truth["true_incremental_share"] = (
        truth["true_incremental_conversions"] / truth["true_incremental_conversions"].sum()
    ).round(6)
    truth["credit_if_last_touch_were_truth"] = None  # filled in by the engine
    return converted, u, truth


# ------------------------------------------------------------------- records
def build_sessions_and_events(
    sequences: list[list[int]],
    converted: np.ndarray,
    users: pd.DataFrame,
    rng: np.random.Generator,
    start: pd.Timestamp,
    days: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Turn abstract touch sequences into sessions, funnel events, and orders."""
    ch_names = C.CHANNEL_NAMES
    sess_rows, event_rows, order_rows = [], [], []
    sid = eid = oid = 0

    devices = list(C.DEVICE_MIX)
    device_p = np.array([C.DEVICE_MIX[d] for d in devices])

    for i, seq in enumerate(sequences):
        user_id = int(users.at[i, "user_id"])
        region = users.at[i, "region"]
        device = devices[int(rng.choice(len(devices), p=device_p))]
        # Journeys start uniformly through the year; touches land days apart.
        t0 = int(rng.integers(0, max(days - 45, 1)))
        gaps = np.cumsum(rng.integers(0, 9, size=len(seq)))
        did_convert = bool(converted[i])

        for k, ch_idx in enumerate(seq):
            is_last = k == len(seq) - 1
            day = min(t0 + int(gaps[k]), days - 1)
            ts = start + pd.Timedelta(days=day, hours=int(rng.integers(6, 23)))
            sid += 1
            clicked = ch_names[ch_idx] != "display" or rng.random() < 0.35
            sess_rows.append(
                {
                    "session_id": sid,
                    "user_id": user_id,
                    "session_ts": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "date_key": int(ts.strftime("%Y%m%d")),
                    "channel_id": ch_idx + 1,
                    "channel": ch_names[ch_idx],
                    "device": device,
                    "region": region,
                    "touch_position": k + 1,
                    "touches_in_journey": len(seq),
                    "is_converting_session": int(did_convert and is_last),
                    "is_click": int(clicked),
                }
            )

            # Funnel. The converting session walks all the way; others drop out.
            reached = ["session_start"]
            if did_convert and is_last:
                reached = list(C.FUNNEL_STEPS)
            else:
                if rng.random() < C.FUNNEL_PASS["product_view"]:
                    reached.append("product_view")
                    if rng.random() < C.FUNNEL_PASS["add_to_cart"]:
                        reached.append("add_to_cart")
                        pass_rate = C.FUNNEL_PASS["checkout_start"]
                        if device == "mobile":
                            pass_rate *= C.MOBILE_CHECKOUT_PENALTY
                        if rng.random() < pass_rate:
                            reached.append("checkout_start")
            for step_no, step in enumerate(reached):
                eid += 1
                event_rows.append(
                    {
                        "event_id": eid,
                        "session_id": sid,
                        "user_id": user_id,
                        "event_ts": (ts + pd.Timedelta(minutes=step_no * 3)).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        "date_key": int(ts.strftime("%Y%m%d")),
                        "event_name": step,
                        "funnel_step": C.FUNNEL_STEPS.index(step) + 1,
                        "device": device,
                        "channel": ch_names[ch_idx],
                    }
                )

            if did_convert and is_last:
                n_orders = 1
                while (
                    n_orders < C.MAX_REPEAT_ORDERS
                    and rng.random() < C.REPEAT_PURCHASE_RATE
                ):
                    n_orders += 1
                for o in range(n_orders):
                    oid += 1
                    o_ts = ts + pd.Timedelta(days=int(o * rng.integers(18, 55)))
                    if o_ts > start + pd.Timedelta(days=days - 1):
                        break
                    revenue = float(
                        np.round(rng.lognormal(np.log(C.AOV_MEAN), C.AOV_SIGMA), 2)
                    )
                    order_rows.append(
                        {
                            "order_id": oid,
                            "user_id": user_id,
                            "session_id": sid if o == 0 else None,
                            "order_ts": o_ts.strftime("%Y-%m-%d %H:%M:%S"),
                            "date_key": int(o_ts.strftime("%Y%m%d")),
                            "revenue": revenue,
                            "gross_margin": round(revenue * C.GROSS_MARGIN, 2),
                            "is_first_order": int(o == 0),
                            "region": region,
                            "device": device,
                        }
                    )

    return (
        pd.DataFrame(sess_rows),
        pd.DataFrame(event_rows),
        pd.DataFrame(order_rows),
    )


def build_spend(sessions: pd.DataFrame, dim_channel: pd.DataFrame) -> pd.DataFrame:
    """
    Spend follows clicks at each channel's CPC, with day-level noise. Only paid
    channels cost money — which is what makes the ROAS comparison in
    sql/04_channel_efficiency.sql interesting rather than trivial.
    """
    rng = np.random.default_rng(C.SEED + 5)
    clicks = (
        sessions[sessions["is_click"] == 1]
        .groupby(["date_key", "channel"], as_index=False)
        .size()
        .rename(columns={"size": "clicks"})
    )
    meta = dim_channel.set_index("channel")
    clicks["channel_id"] = clicks["channel"].map(meta["channel_id"])
    clicks["is_paid"] = clicks["channel"].map(meta["is_paid"])
    cpc = clicks["channel"].map(meta["cost_per_click"]).to_numpy()
    noise = rng.normal(1.0, 0.09, size=len(clicks)).clip(0.7, 1.4)
    clicks["spend"] = np.where(
        clicks["is_paid"] == 1, np.round(clicks["clicks"] * cpc * noise, 2), 0.0
    )
    # Display buys impressions, not clicks; the ratio drives its awful CTR.
    imp_mult = np.where(clicks["channel"] == "display", 90, 14)
    clicks["impressions"] = (clicks["clicks"] * imp_mult * noise).round().astype(int)
    return clicks[
        ["date_key", "channel_id", "channel", "impressions", "clicks", "spend"]
    ].sort_values(["date_key", "channel_id"], ignore_index=True)


def build_geo_experiment(start: pd.Timestamp) -> pd.DataFrame:
    """
    An eight-week geo holdout on one channel, generated separately from the
    observational data above and with its own seed.

    This is deliberate: the holdout is an *experiment*, not a slice of the
    main tables. Suppressing a channel inside the observational data would
    contaminate the attribution bake-off with a regional artefact, and the
    experiment's whole job is to be an independent check on it.
    """
    rng = np.random.default_rng(C.EXPERIMENT_SEED)
    lift = C.CHANNELS[C.EXPERIMENT_CHANNEL]["true_lift"]
    base_n = C.EXPERIMENT_SESSIONS_PER_GEO_WEEK
    half = C.EXPERIMENT_WEEKS // 2
    rows = []
    for geo in C.EXPERIMENT_GEOS:
        held = geo in C.EXPERIMENT_HOLDOUT_GEOS
        # Geos differ in size and baseline conversion; the experiment has to
        # survive that, which is why the readout is a difference-in-differences.
        scale = float(rng.uniform(0.7, 1.5))
        base_cvr = float(rng.uniform(0.031, 0.046))
        for week in range(C.EXPERIMENT_WEEKS):
            in_period = week >= half       # first half pre, second half treatment
            suppressed = held and in_period
            sessions = int(rng.normal(base_n * scale, 0.045 * base_n * scale))
            # Seasonal drift hits every geo equally — that common shock is
            # exactly what a naive pre/post comparison would misread as effect.
            season = 1.0 + 0.05 * np.sin(week / 2.4)
            cvr = base_cvr * season * ((1 - lift) if suppressed else 1.0)
            conversions = int(rng.binomial(sessions, cvr))
            rows.append(
                {
                    "geo": geo,
                    "week": week,
                    "week_start": (start + pd.Timedelta(weeks=week)).strftime("%Y-%m-%d"),
                    "period": "treatment" if in_period else "pre",
                    "in_holdout": int(held),
                    "channel_suppressed": int(suppressed),
                    "sessions": sessions,
                    "conversions": conversions,
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------- main
def main() -> None:
    rng = np.random.default_rng(C.SEED)
    start = pd.Timestamp(C.START_DATE)
    days = C.MONTHS * 30

    print(f"Generating {C.N_USERS:,} user journeys over {C.MONTHS} months (seed {C.SEED})")

    dim_channel = build_dim_channel()
    dim_date = build_dim_date(start, days)

    users = pd.DataFrame(
        {
            "user_id": np.arange(1, C.N_USERS + 1),
            "region": rng.choice(C.REGIONS, size=C.N_USERS),
        }
    )

    counts, sequences = build_journeys(rng)
    converted, _u, truth = resolve_conversions(counts, rng)

    users["converted"] = converted.astype(int)
    users["first_touch_channel"] = [C.CHANNEL_NAMES[s[0]] for s in sequences]
    users["last_touch_channel"] = [C.CHANNEL_NAMES[s[-1]] for s in sequences]
    users["touches"] = [len(s) for s in sequences]

    sessions, events, orders = build_sessions_and_events(
        sequences, converted, users, rng, start, days
    )
    spend = build_spend(sessions, dim_channel)
    experiment = build_geo_experiment(start)

    # The journey path, one row per user, is what the attribution models read.
    journeys = pd.DataFrame(
        {
            "user_id": users["user_id"],
            "path": [">".join(C.CHANNEL_NAMES[c] for c in s) for s in sequences],
            "converted": converted.astype(int),
            "region": users["region"],
        }
    )

    print("\nWriting data/")
    _write(dim_channel, "dim_channel.csv")
    _write(dim_date, "dim_date.csv")
    _write(users, "dim_user.csv")
    _write(journeys, "fact_journeys.csv")
    _write(sessions, "fact_sessions.csv")
    _write(events, "fact_events.csv")
    _write(orders, "fact_orders.csv")
    _write(spend, "fact_spend.csv")
    _write(experiment, "experiment_geo_weekly.csv")
    _write(truth, "ground_truth_incrementality.csv")

    cr = converted.mean()
    print(
        f"\n  {converted.sum():,} converting users ({cr:.2%}), "
        f"{len(orders):,} orders, ${spend['spend'].sum():,.0f} spend"
    )
    print("\n  Planted truth (incremental conversions, not a 100% split):")
    for _, r in truth.sort_values("true_incremental_conversions", ascending=False).iterrows():
        print(
            f"    {r['channel']:<16} lift {r['true_lift']:.3f}  "
            f"incremental {r['true_incremental_conversions']:>5,}  "
            f"share {r['true_incremental_share']:.1%}"
        )


if __name__ == "__main__":
    main()
