"""culture_rules.model.refs is a model module: it imports nothing from the engine (#4)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

REFS = Path(__file__).resolve().parents[2] / "culture_rules" / "model" / "refs.py"


def test_model_refs_imports_no_culture_rules_module():
    tree = ast.parse(REFS.read_text(encoding="utf-8"))
    imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)] + [
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    ]
    assert [m for m in imported if m.startswith("culture_rules")] == []


def test_model_validate_does_not_pull_in_the_engine():
    code = (
        "import sys, culture_rules.model.validate, culture_rules.model.refs; "
        "print(sorted(m for m in sys.modules if m.startswith('culture_rules.engine')))"
    )
    out = subprocess.run(  # noqa: S603 - fixed argv, this interpreter
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "[]"


def test_engine_modules_import_the_moved_helpers():
    from culture_rules.engine import ruleset, runs
    from culture_rules.model import refs

    assert runs.resolve_refs is refs.resolve_refs
    assert ruleset.scanned_strings is refs.scanned_strings
