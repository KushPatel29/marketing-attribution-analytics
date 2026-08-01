"""
Build the whole pipeline once per session, then let every test read its output.

Regenerating per-test would be slower and, worse, would hide any state that
leaks between stages — running it exactly once is also a check that the stages
compose in the documented order.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

STAGES = [
    "data_generator/generate_marketing_data.py",
    "saas/generate_gtm_data.py",
    "engine/run_analytics.py",
    "attribution/evaluate.py",
    "experiments/incrementality.py",
    "saas/gtm_metrics.py",
]


@pytest.fixture(scope="session", autouse=True)
def pipeline() -> None:
    for stage in STAGES:
        result = subprocess.run(
            [sys.executable, stage], cwd=ROOT, capture_output=True, text=True
        )
        assert result.returncode == 0, f"{stage} failed:\n{result.stdout}\n{result.stderr}"


@pytest.fixture(scope="session")
def data():
    def _load(name: str) -> pd.DataFrame:
        return pd.read_csv(ROOT / "data" / f"{name}.csv")

    return _load


@pytest.fixture(scope="session")
def out():
    def _load(name: str) -> pd.DataFrame:
        return pd.read_csv(ROOT / "output" / f"{name}.csv")

    return _load
