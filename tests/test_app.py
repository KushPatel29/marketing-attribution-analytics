"""
Guards on the Streamlit console.

The console is the only artefact here a recruiter is likely to open, and it is
the one thing CI cannot fully exercise — so what can be checked statically, is.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app" / "streamlit_app.py"


def test_app_parses():
    ast.parse(APP.read_text(encoding="utf-8"))


def test_app_does_not_use_tabs():
    """
    st.tabs measures charts and dataframes while their container is hidden, so
    every section except the one active on first load rendered as an empty box
    on the deployed app. Navigation is a radio for that reason; this fails if
    anyone reintroduces tabs without knowing why they were removed.
    """
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "tabs"
    ]
    # Matched on the call, not on the text: the comment explaining why tabs were
    # removed necessarily contains the word.
    assert not calls, "st.tabs zero-widths charts in inactive tabs — use the radio"


def test_app_reads_only_committed_outputs():
    """
    The app must not recompute metrics; it reads CSVs the pipeline wrote and the
    tests assert on. If it starts importing the engine, a second implementation
    has appeared and the two can disagree.
    """
    src = APP.read_text(encoding="utf-8")
    for forbidden in ("from engine", "from attribution", "from experiments", "import engine"):
        assert forbidden not in src, f"app imports pipeline code: {forbidden}"


def test_every_csv_the_app_loads_is_committed():
    """A file the app reads but git does not track is a crash on a fresh clone."""
    import re
    import subprocess

    src = APP.read_text(encoding="utf-8")
    names = set(re.findall(r'load\("([a-z_]+)"(?:,\s*"([a-z]+)")?\)', src))
    tracked = set(
        subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.split()
    )
    assert names, "no load() calls found — has the app been restructured?"
    for name, folder in names:
        path = f"{folder or 'output'}/{name}.csv"
        assert path in tracked, f"{path} is read by the app but not tracked by git"
