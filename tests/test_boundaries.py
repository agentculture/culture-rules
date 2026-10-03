"""Boundary guards: culture_rules must not couple to sibling projects (c31, c34, c35, c59)."""

from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PKG = ROOT / "culture_rules"

FORBIDDEN = ("culture_nodes", "workledger", "agenda", "protocols", "callsmith", "eidetic")
# Distribution-name spellings (hyphen/underscore variants) for pyproject checks.
FORBIDDEN_DISTS = FORBIDDEN + ("culture-nodes", "workledger-cli", "protocols-cli", "eidetic-cli")


def _top_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [a.name.split(".")[0] for a in node.names]
    if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
        return [node.module.split(".")[0]]
    return []


def find_forbidden_imports(source: str, forbidden=FORBIDDEN) -> list[str]:
    """Imports of forbidden top-level modules, anywhere in the source (incl. lazy/in-function)."""
    hits = []
    for node in ast.walk(ast.parse(source)):
        hits += [n for n in _top_names(node) if n in forbidden]
        # importlib.import_module("x") / __import__("x") with a literal
        if isinstance(node, ast.Call) and node.args:
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            arg = node.args[0]
            if (
                name in ("import_module", "__import__")
                and isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
            ):
                top = arg.value.split(".")[0]
                if top in forbidden:
                    hits.append(top)
    return hits


def find_eidetic_references(source: str) -> list[str]:
    """Code-level references to eidetic (names, attributes, non-docstring string literals)."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    hits = []
    for node in ast.walk(tree):
        text = None
        if isinstance(node, ast.Name):
            text = node.id
        elif isinstance(node, ast.Attribute):
            text = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                text = node.value
        if text and "eidetic" in text.lower():
            hits.append(text)
    return hits


def _py_files() -> list[Path]:
    files = sorted(PKG.rglob("*.py"))
    assert files, "no sources found under culture_rules/"
    return files


# --- scanner self-tests (prove the guard can fail) -------------------------------------------


@pytest.mark.parametrize("name", FORBIDDEN)
@pytest.mark.parametrize(
    "template",
    ["import {n}", "import {n}.sub as x", "from {n} import y", "from {n}.sub import y"],
)
def test_scanner_detects_forbidden_import(name, template):
    assert find_forbidden_imports(template.format(n=name)) == [name]


def test_scanner_detects_lazy_and_dynamic_imports():
    src = "def f():\n    import workledger\n    importlib.import_module('agenda.x')\n"
    assert find_forbidden_imports(src) == ["workledger", "agenda"]


def test_scanner_ignores_docstrings_comments_and_relative_imports():
    src = (
        '"""Talks about eidetic, agenda and protocols."""\n'
        "# import callsmith\n"
        "from . import agenda\n"
    )
    assert find_forbidden_imports(src) == []
    assert find_eidetic_references(src) == []


def test_scanner_detects_eidetic_run_state_use():
    assert find_eidetic_references("store = eidetic_client.write(run)")
    assert find_eidetic_references("subprocess.run(['eidetic', 'remember'])")
    assert find_eidetic_references("x = obj.eidetic")


# --- real guards over culture_rules/ -----------------------------------------------------------


def test_culture_rules_imports_no_sibling_projects():
    offenders = {}
    for path in _py_files():
        hits = find_forbidden_imports(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders, f"forbidden sibling-project imports: {offenders}"


def test_culture_rules_does_not_use_eidetic_for_run_state():
    offenders = {}
    for path in _py_files():
        hits = find_eidetic_references(path.read_text(encoding="utf-8"))
        if hits:
            offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders, f"eidetic referenced in code: {offenders}"


def test_pyproject_declares_no_sibling_dependencies():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    reqs = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        reqs += extra
    bad = []
    for req in reqs:
        base = req.lower()
        for ch in "<>=!~[; ":
            base = base.split(ch)[0]
        if base.replace("_", "-") in {d.replace("_", "-") for d in FORBIDDEN_DISTS}:
            bad.append(req)
    assert not bad, f"sibling projects declared as dependencies: {bad}"


def test_package_imports_with_culture_nodes_absent():
    """Importing every module works when culture_nodes cannot be imported (None in sys.modules)."""
    mods = []
    for path in _py_files():
        rel = path.relative_to(ROOT).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__main__":
            continue
        if parts[-1] == "__init__":
            parts.pop()
        mods.append(".".join(parts))
    code = (
        "import sys, importlib\n"
        "sys.modules['culture_nodes'] = None\n"
        f"for m in {mods!r}:\n"
        "    try:\n"
        "        importlib.import_module(m)\n"
        "    except ImportError as e:\n"
        "        if 'culture_nodes' in str(e):\n"
        "            raise\n"
        "        # missing optional third-party extras are fine\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr


def test_culture_nodes_not_installed_in_test_env():
    """The suite is expected to run with culture-nodes absent (criterion 2)."""
    import importlib.util

    assert importlib.util.find_spec("culture_nodes") is None
