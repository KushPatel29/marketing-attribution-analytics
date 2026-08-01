"""
Load the generated CSVs into SQLite and execute the sql/ files verbatim.

The point of this shape: the SQL committed in `sql/` is the SQL that runs. It
is not a Python re-implementation that happens to resemble the .sql files, and
it is not documentation that drifts. A reviewer can read `sql/02_funnel.sql`
and know that exact text produced `output/funnel_overall.csv`.

    python engine/run_analytics.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config as C  # noqa: E402

# Loaded in dependency order; names match the DDL in sql/01_schema.sql.
TABLES = [
    "dim_channel",
    "dim_date",
    "dim_user",
    "fact_journeys",
    "fact_sessions",
    "fact_events",
    "fact_orders",
    "fact_spend",
    "ground_truth_incrementality",
]

# 01 is DDL for reference; the loader creates the tables from the dataframes,
# so the analysis files are what we execute.
ANALYSIS_FILES = [
    "02_funnel.sql",
    "03_cohorts_ltv.sql",
    "04_channel_efficiency.sql",
    "05_attribution_heuristics.sql",
]

# Tables produced by the SQL that are worth exporting for the app and the tests.
EXPORTS = [
    "funnel_overall",
    "funnel_by_device",
    "funnel_device_gap",
    "funnel_by_entry_channel",
    "cohort_retention",
    "cohort_ltv",
    "repeat_rate_by_channel",
    "channel_efficiency",
    "channel_monthly",
    "attribution_heuristics",
    "channel_journey_roles",
    "journey_length_distribution",
]


def load_warehouse() -> sqlite3.Connection:
    """In-memory SQLite holding the whole marketing warehouse."""
    missing = [t for t in TABLES if not (C.DATA / f"{t}.csv").exists()]
    if missing:
        raise FileNotFoundError(
            f"missing {missing} — run data_generator/generate_marketing_data.py first"
        )
    con = sqlite3.connect(":memory:")
    for name in TABLES:
        pd.read_csv(C.DATA / f"{name}.csv").to_sql(name, con, index=False)
    # Attribution queries hit fact_sessions by user repeatedly.
    con.execute("CREATE INDEX ix_sessions_user ON fact_sessions(user_id)")
    con.execute("CREATE INDEX ix_events_session ON fact_events(session_id)")
    return con


def run_sql_files(con: sqlite3.Connection, files: list[str] | None = None) -> None:
    for name in files or ANALYSIS_FILES:
        sql = (C.ROOT / "sql" / name).read_text(encoding="utf-8")
        con.executescript(sql)
        print(f"  ran sql/{name}")


def export(con: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    C.OUT.mkdir(parents=True, exist_ok=True)
    out = {}
    for name in EXPORTS:
        df = pd.read_sql_query(f"SELECT * FROM {name}", con)
        df.to_csv(C.OUT / f"{name}.csv", index=False, lineterminator="\n")
        out[name] = df
    return out


def main() -> None:
    print("Loading warehouse")
    con = load_warehouse()
    print("Executing sql/")
    run_sql_files(con)
    tables = export(con)
    print(f"\nWrote {len(tables)} tables to output/")

    eff = tables["channel_efficiency"]
    print("\n  Last-touch view (what the default dashboard would report):")
    for _, r in eff.iterrows():
        roas = f"{r['roas_last_touch']:.2f}x" if pd.notna(r["roas_last_touch"]) else "  n/a"
        print(
            f"    {r['channel']:<16} conv {int(r['last_touch_conversions']):>5,}  "
            f"share {r['last_touch_share']:.1%}  spend ${r['spend']:>9,.0f}  ROAS {roas}"
        )

    gap = tables["funnel_device_gap"]
    worst = gap.iloc[-1]
    print(
        f"\n  Checkout-start rate is worst on {worst['device']} at "
        f"{worst['checkout_start_rate']:.1f}% — "
        f"{worst['points_behind_best_device']:.1f} points behind the best device."
    )


if __name__ == "__main__":
    main()
