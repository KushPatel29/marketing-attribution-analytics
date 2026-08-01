"""
Every constant the simulation and the analysis depend on, in one place.

The channel table below is the heart of the repo. `true_lift` is the *real*
incremental effect each channel has on a user's probability of converting —
the thing no marketing team can observe and every attribution model is trying
to estimate. Because this data is synthetic, we know it exactly, which is what
makes the attribution bake-off in `attribution/` measurable rather than a
matter of opinion.

`close_weight` is deliberately decoupled from `true_lift`: it controls how
often a channel shows up *last* in a journey. Direct traffic has the highest
close weight and almost the lowest true lift, because "direct" is mostly a
label for demand that something else created. That single row is the reason
last-touch attribution is wrong in a way that costs money.
"""

from __future__ import annotations

SEED = 71

# ---------------------------------------------------------------- simulation
N_USERS = 14_000
MONTHS = 12
START_DATE = "2025-08-01"

# Baseline probability that a user converts having seen no marketing at all.
BASELINE_CONVERSION = 0.020

# A channel stops adding incremental lift after this many touches. Without a
# cap, a user retargeted 40 times would converge on a 100% conversion rate,
# which is not how advertising works.
MAX_EFFECTIVE_TOUCHES = 3

# channel -> (group, is_paid, true_lift, open_weight, close_weight, cpc)
#   open_weight  : relative chance of starting a journey
#   close_weight : relative chance of being the final touch before conversion
#   cpc          : cost per click, used to build the spend table
CHANNELS: dict[str, dict] = {
    "paid_search":    {"group": "Paid",   "is_paid": True,  "true_lift": 0.055,
                       "open_weight": 1.4, "close_weight": 2.2, "cpc": 2.35},
    "paid_social":    {"group": "Paid",   "is_paid": True,  "true_lift": 0.020,
                       "open_weight": 2.6, "close_weight": 0.7, "cpc": 0.95},
    "display":        {"group": "Paid",   "is_paid": True,  "true_lift": 0.010,
                       "open_weight": 3.0, "close_weight": 0.4, "cpc": 0.55},
    "affiliate":      {"group": "Paid",   "is_paid": True,  "true_lift": 0.030,
                       "open_weight": 0.9, "close_weight": 1.3, "cpc": 1.60},
    "email":          {"group": "Owned",  "is_paid": False, "true_lift": 0.045,
                       "open_weight": 0.3, "close_weight": 1.8, "cpc": 0.04},
    "organic_search": {"group": "Earned", "is_paid": False, "true_lift": 0.035,
                       "open_weight": 1.8, "close_weight": 1.1, "cpc": 0.00},
    # The trap. Enormous closing presence, almost no incremental effect.
    "direct":         {"group": "Owned",  "is_paid": False, "true_lift": 0.005,
                       "open_weight": 0.6, "close_weight": 3.0, "cpc": 0.00},
}

CHANNEL_NAMES = list(CHANNELS)

# ------------------------------------------------------------------ commerce
AOV_MEAN = 96.0          # average order value, lognormal
AOV_SIGMA = 0.45
GROSS_MARGIN = 0.42
REPEAT_PURCHASE_RATE = 0.31   # chance a converted user buys again in a month
MAX_REPEAT_ORDERS = 6

# --------------------------------------------------------------- the funnel
# Conditional pass-through for a session that does not convert outright.
# These are the step rates the SQL in sql/02_funnel.sql re-derives from events.
FUNNEL_STEPS = ["session_start", "product_view", "add_to_cart", "checkout_start", "purchase"]
FUNNEL_PASS = {
    "product_view": 0.62,
    "add_to_cart": 0.34,
    "checkout_start": 0.55,
}
# Mobile converts worse at checkout — a real and boringly common pattern, and
# the thing sql/02_funnel.sql is designed to surface by device.
MOBILE_CHECKOUT_PENALTY = 0.72
DEVICE_MIX = {"mobile": 0.58, "desktop": 0.34, "tablet": 0.08}

REGIONS = ["BC", "AB", "ON", "QC", "Atlantic", "Prairies"]

# ------------------------------------------------------------- experiment
# A geo holdout on paid_search: we switch it off in two regions for eight weeks
# and measure what actually changes. See experiments/incrementality.py.
#
# The channel is not chosen for convenience. A holdout has to detect a change
# in conversion rate of (true_lift x baseline), and for paid_social that is
# 0.020 x 0.04 = 8 basis points, which needs roughly 940,000 sessions per arm
# to see at 80% power. paid_search moves 5.5%, needs ~125,000, and is testable
# at a realistic traffic level. Choosing a channel your traffic can actually
# power is the first decision in an incrementality test, and the one most
# often skipped — experiments/incrementality.py shows the arithmetic for both.
EXPERIMENT_CHANNEL = "paid_search"
EXPERIMENT_SEED = 72
EXPERIMENT_ALPHA = 0.05
EXPERIMENT_POWER = 0.80
EXPERIMENT_WEEKS = 16                 # 8 pre, 8 treatment
EXPERIMENT_SESSIONS_PER_GEO_WEEK = 5_200

# Assignment is at the geo, not the region: 12 DMA-style units, 4 held out.
# This matters for the interval, not just the point estimate — the confidence
# interval is bootstrapped over geo-weeks because the geo is the unit that was
# randomised, so widening the test means more geos, not more sessions inside
# the geos you already have.
EXPERIMENT_GEOS = [f"GEO-{i:02d}" for i in range(1, 21)]
EXPERIMENT_HOLDOUT_GEOS = [f"GEO-{i:02d}" for i in range(2, 21, 2)]  # 10 of 20
# The geo test reads aggregate regional traffic; the journey-level tables are a
# 14,000-user sample of the same business. Different grains on purpose — this
# is how the two analyses are actually sourced in practice.

# ------------------------------------------------------------------- paths
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"
DOCS = ROOT / "docs"
