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


def test_app_imports_nothing_from_the_project():
    """
    The app reads files; it does not import project code — not the engine, not
    the models, and *not* `config`.

    The engine rule is about duplication: a second implementation of a metric
    can disagree with the first. The `config` rule is about deployment, and it
    was learned the hard way. Streamlit re-executes the script on every
    interaction but keeps `sys.modules` across runs, so after a deploy the new
    script ran against the config module imported by the *previous* version and
    raised AttributeError on a constant that was right there in the file. The
    scalars now come from output/run_metadata.csv.

    Checked on the AST so a mention in a comment or docstring — like the one
    above — cannot trip it.
    """
    tree = ast.parse(APP.read_text(encoding="utf-8"))
    project = {"config", "engine", "attribution", "experiments", "saas", "analytics",
               "data_generator"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    leaked = imported & project
    assert not leaked, f"app imports project code: {sorted(leaked)}"


def test_app_does_not_manipulate_sys_path():
    """
    The sys.path.insert existed only to make `import config` work. With the
    import gone the path hack is dead weight, and leaving it invites the import
    back.
    """
    src = APP.read_text(encoding="utf-8")
    assert "sys.path" not in src


def test_run_metadata_carries_what_the_app_needs():
    """The pipeline must actually write the scalars the console now depends on."""
    import csv

    path = ROOT / "output" / "run_metadata.csv"
    assert path.exists(), "pipeline did not write output/run_metadata.csv"
    row = next(iter(csv.DictReader(path.open(encoding="utf-8"))))
    assert {"seed", "saas_seed", "experiment_channel"} <= set(row)
    assert row["experiment_channel"]
    assert int(row["seed"]) and int(row["saas_seed"])


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
