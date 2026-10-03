"""Criterion 3: the CLI / API-client / MCP layers never import a store driver.

They talk to the HTTP API; only the server process holds the store. Checked statically (every
import statement, including lazy ones) and dynamically (importing every module of those
packages leaves pymongo and culture_rules.store.mongo out of sys.modules).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAYERS = ("cli", "client", "mcp")
DRIVERS = ("pymongo", "bson", "motor", "culture_rules.store.mongo")


def _present() -> list[Path]:
    return [
        ROOT / "culture_rules" / name for name in LAYERS if (ROOT / "culture_rules" / name).is_dir()
    ]


def _imports(source: str) -> list[str]:
    out = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            out += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out += [node.module] + [f"{node.module}.{a.name}" for a in node.names]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                out.append(node.args[0].value)
    return out


def _is_driver(module: str) -> bool:
    return any(module == d or module.startswith(d + ".") for d in DRIVERS)


def test_static_scan_finds_a_planted_driver_import():
    assert _is_driver("pymongo.errors")
    assert any(
        _is_driver(m) for m in _imports("def f():\n    from culture_rules.store import mongo\n")
    )
    assert any(
        _is_driver(m) for m in _imports("import importlib\nimportlib.import_module('pymongo')")
    )


def test_cli_client_mcp_never_import_a_store_driver_statically():
    assert _present(), "culture_rules/cli must exist"
    offenders = []
    for pkg in _present():
        for path in sorted(pkg.rglob("*.py")):
            hits = [m for m in _imports(path.read_text(encoding="utf-8")) if _is_driver(m)]
            offenders += [f"{path.relative_to(ROOT)}: {m}" for m in hits]
    assert offenders == []


@pytest.mark.parametrize("layer", LAYERS)
def test_importing_every_module_of_the_layer_loads_no_driver(layer):
    if not (ROOT / "culture_rules" / layer).is_dir():
        pytest.skip(f"culture_rules/{layer} does not exist yet")
    code = (
        "import importlib, pkgutil, sys\n"
        f"pkg = importlib.import_module('culture_rules.{layer}')\n"
        "for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + '.'):\n"
        "    importlib.import_module(m.name)\n"
        f"bad = [m for m in sys.modules if m.split('.')[0] in ('pymongo', 'bson', 'motor')"
        " or m == 'culture_rules.store.mongo']\n"
        "print(bad)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "[]"
