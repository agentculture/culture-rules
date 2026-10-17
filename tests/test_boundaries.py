"""Boundary guards: culture_rules must not couple to sibling projects (c31, c34, c35, c59)."""

from __future__ import annotations

import ast
import re
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


# Workflow and step `description` / `notes` are display-only text, excluded from the trust digest,
# so the engine, the node daemon and the actors must never read them (c32, h29).
DISPLAY_ONLY_FIELDS = ("description", "notes")
DISPLAY_ONLY_PACKAGES = ("engine", "node", "actors")
# A receiver is a workflow or a step when its tail name says so: `workflow`, `self.workflow`,
# `step`, `wf_doc`, `get_workflow(...)`. Ports, actors, rules and variables are not matched.
_WORKFLOW_LIKE = re.compile(r"workflow|step|(?:^|_)wf", re.IGNORECASE)


def _tail_name(expr: ast.AST) -> str:
    """The name an expression ends on: ``a.b[0]`` gives ``b``, ``get_wf(x)`` gives ``get_wf``."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    if isinstance(expr, ast.Subscript):
        return _tail_name(expr.value)
    if isinstance(expr, ast.Call):
        return _tail_name(expr.func)
    return ""


def _workflow_like_names(tree: ast.AST) -> set[str]:
    """Names bound to a workflow or step (``for step in wf.steps``, ``wf = get_workflow(id)``)."""
    names: set[str] = set()
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if isinstance(node, ast.For):
                targets, value = [node.target], node.iter
            elif isinstance(node, ast.Assign):
                targets, value = node.targets, node.value
            else:
                continue
            if not _WORKFLOW_LIKE.search(_tail_name(value)):
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in names:
                    names.add(target.id)
                    changed = True
    return names


def _is_workflow_like(receiver: ast.AST, names: set[str]) -> bool:
    tail = _tail_name(receiver)
    return bool(_WORKFLOW_LIKE.search(tail)) or tail in names


def find_display_text_reads(source: str) -> list[str]:
    """Reads of a workflow's or step's ``description`` / ``notes``: attribute access, ``.get``,
    subscripts and ``getattr`` with a literal name. Other receivers (actors, ports, variables,
    rules, search hits) are not flagged, so a ``description`` there is fine."""
    tree = ast.parse(source)
    names = _workflow_like_names(tree)
    hits = []
    for node in ast.walk(tree):
        field_name, receiver = None, None
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            if node.attr in DISPLAY_ONLY_FIELDS:
                field_name, receiver = node.attr, node.value
        elif isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
            key = node.slice
            if isinstance(key, ast.Constant) and key.value in DISPLAY_ONLY_FIELDS:
                field_name, receiver = key.value, node.value
        elif isinstance(node, ast.Call) and node.args:
            fn = node.func
            if isinstance(fn, ast.Attribute) and fn.attr == "get":
                key, receiver = node.args[0], fn.value
            elif isinstance(fn, ast.Name) and fn.id == "getattr" and len(node.args) >= 2:
                key, receiver = node.args[1], node.args[0]
            else:
                continue
            if isinstance(key, ast.Constant) and key.value in DISPLAY_ONLY_FIELDS:
                field_name = key.value
        if field_name and _is_workflow_like(receiver, names):
            hits.append(f"line {node.lineno}: {ast.unparse(receiver)}.{field_name}")
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


@pytest.mark.parametrize(
    "src",
    [
        "def f(step):\n    return step.description\n",
        "def f(workflow):\n    return workflow.notes\n",
        "def f(self):\n    return self.workflow.description\n",
        "def f(wf):\n    return wf['notes']\n",
        "def f(wf):\n    return wf.get('description')\n",
        "def f(step):\n    return getattr(step, 'notes', '')\n",
        "def f(run):\n    for s in run.workflow.steps:\n        print(s.description)\n",
        "def f(store, id):\n    wf = store.get_workflow(id)\n    return wf.notes\n",
        "def f(plan):\n    return plan.workflow.get('notes')\n",
    ],
)
def test_display_text_scanner_detects_workflow_and_step_reads(src):
    assert find_display_text_reads(src), src


@pytest.mark.parametrize(
    "src",
    [
        "def f(actor):\n    return actor.description\n",
        "def f(port):\n    return port.description\n",
        "def f(workflow):\n    return workflow.inputs[0].description\n",
        "def f(variable):\n    return variable.notes\n",
        "def f(rule):\n    return rule.get('description')\n",
        "def f(hit):\n    return hit.get('description')\n",
        "def f(chain):\n    return [n.get('text') for n in chain.notes]\n",
        "def f(workflow):\n    return workflow.name\n",
        "# step.description in a comment\nX = 'workflow notes'\n",
    ],
)
def test_display_text_scanner_ignores_other_receivers(src):
    assert find_display_text_reads(src) == []


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


def test_engine_node_actors_never_read_workflow_or_step_text():
    """Workflow and step description/notes are display-only: no engine, node or actor reads them."""
    offenders = {}
    for package in DISPLAY_ONLY_PACKAGES:
        for path in sorted((PKG / package).rglob("*.py")):
            hits = find_display_text_reads(path.read_text(encoding="utf-8"))
            if hits:
                offenders[str(path.relative_to(ROOT))] = hits
    assert not offenders, f"workflow/step description or notes read outside display: {offenders}"


def test_no_summarize_verb_or_tool_and_no_summary_by_field():
    """Drafting is not a verb: no CLI verb or MCP tool named summarize, no summary_by field."""
    from culture_rules.cli.verbs import REGISTRY

    names = []
    for verb in REGISTRY.verbs():
        names += [verb.name, verb.tool_name]
    assert not [n for n in names if "summarize" in n.lower()], names
    model_hits = [
        str(path.relative_to(ROOT))
        for path in sorted((PKG / "model").rglob("*.py"))
        if "summary_by" in path.read_text(encoding="utf-8")
    ]
    assert not model_hits, f"summary_by field in the model: {model_hits}"


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
