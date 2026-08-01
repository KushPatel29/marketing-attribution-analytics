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

# ==========================================================================
# ACT TWO — the same questions asked of a B2B SaaS go-to-market motion
# ==========================================================================
# Act one is e-commerce: a session converts or it doesn't, and the money
# arrives once. A SaaS motion breaks both assumptions. Revenue is recurring,
# so a customer can be worth more next year than this one; and a "conversion"
# is a months-long opportunity moving through stages, owned by a rep carrying
# a quota. Almost every GTM metric that matters exists because of those two
# differences, so the second dataset models them directly.

SAAS_SEED = 88
SAAS_START = "2024-07-01"
SAAS_MONTHS = 24
N_ACCOUNTS = 2_600

SEGMENTS = {
    # segment -> (share of accounts, mean new-business ARR, win rate, sales-cycle days)
    "SMB":         {"share": 0.55, "arr_mean": 11_000,  "win_rate": 0.28, "cycle_days": 34},
    "Mid-Market":  {"share": 0.33, "arr_mean": 42_000,  "win_rate": 0.22, "cycle_days": 76},
    "Enterprise":  {"share": 0.12, "arr_mean": 168_000, "win_rate": 0.17, "cycle_days": 148},
}

# Salesforce-shaped stage ladder. `is_closed` / `is_won` mirror the standard
# OpportunityStage fields so the SQL reads the way it would against a real CRM.
SAAS_STAGES = [
    ("Prospecting",     1, False, False),
    ("Discovery",       2, False, False),
    ("Demo",            3, False, False),
    ("Proposal",        4, False, False),
    ("Negotiation",     5, False, False),
    ("Closed Won",      6, True,  True),
    ("Closed Lost",     7, True,  False),
]

LOSS_REASONS = ["Price", "Competitor", "No decision", "Lost to status quo", "Timing"]
COMPETITORS = ["Northwind", "Helioscope", "Kestrel", "(none)"]
INDUSTRIES = ["SaaS", "Manufacturing", "Retail", "Healthcare", "Financial services", "Logistics"]

N_TERRITORIES = 8
REP_RAMP_MONTHS = 4          # a new rep carries a reduced quota while ramping

# Quotas are set from the productivity the simulation actually produces, so
# blended attainment lands near 85% -- roughly where a real sales org sits.
# Setting them by wishful thinking instead is how you get a capacity plan that
# says every rep is failing, which is a modelling error dressed as a finding.
QUOTA_PER_REP = {"SMB": 528_000, "Mid-Market": 696_000, "Enterprise": 620_000}

# How many opportunities an account generates, by segment. SMB reps run many
# small fast deals; Enterprise reps run few large slow ones, and that ratio is
# most of why the two motions cannot share a dashboard.
OPPS_PER_ACCOUNT = {"SMB": 2.6, "Mid-Market": 1.5, "Enterprise": 1.2}

# Lead creation grows through the window. A flat rate would leave almost no
# deals in flight at the end, and pipeline coverage would read near zero for
# reasons that are an artefact of the window rather than the business.
LEAD_GROWTH_PER_MONTH = 0.045

# Product-qualified accounts really do convert better here -- planted, so the
# PLG-versus-sales-led comparison is measuring something. The README still says
# out loud that self-serve accounts self-select, so the observed gap overstates
# the causal one.
PQL_WIN_RATE_MULTIPLIER = 1.45

# Renewal-time behaviour, applied per subscription anniversary.
RENEWAL = {
    "SMB":        {"churn": 0.22, "contract": 0.09, "expand": 0.24, "expand_pct": 0.18},
    "Mid-Market": {"churn": 0.12, "contract": 0.07, "expand": 0.34, "expand_pct": 0.22},
    "Enterprise": {"churn": 0.06, "contract": 0.05, "expand": 0.44, "expand_pct": 0.27},
}

# Product-led motion running alongside sales: self-serve signup, then the
# activation event, then the usage threshold that makes an account a PQL.
# The book of business already on the shelf when the reporting window opens.
# Net revenue retention is a statement about existing customers, so without
# a starting base it would be computed over a handful of accidents.
N_LEGACY_SUBSCRIPTIONS = 260

PLG_SIGNUP_RATE = 0.42        # share of accounts that arrive self-serve first
PLG_ACTIVATION_RATE = 0.51    # of signups, reach the activation milestone
PLG_PQL_RATE = 0.29           # of activated, cross the qualification threshold
PLG_PQL_TO_OPP = 0.61         # of PQLs, become a sales opportunity

# Sales & marketing cost, used for CAC payback and the magic number. Split so
# the marketing half can be re-cut by the attribution models from act one.
# Fully loaded per quota-carrying rep: their comp plus the SDR, sales
# engineering, management and tooling that sit behind them. Costing a rep at
# base salary alone is the single most common way a CAC comes out flattering.
SALES_COST_PER_REP_MONTH = 46_000
MARKETING_COST_PER_MONTH = 265_000
GROSS_MARGIN_SAAS = 0.78

# ------------------------------------------------------------------- paths
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "output"
DOCS = ROOT / "docs"
