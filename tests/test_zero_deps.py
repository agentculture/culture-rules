"""The runtime stays dependency-free; the github/discord extras, where used at all, import
lazily (spec c25, c33): every culture_rules module imports with them blocked."""

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKED = ("jwt", "cryptography", "discord")

IMPORT_CHECK = """
import importlib, pkgutil, sys
for name in {blocked!r}:
    sys.modules[name] = None
import culture_rules
seen = 0
for mod in pkgutil.walk_packages(culture_rules.__path__, "culture_rules."):
    if mod.name.rsplit(".", 1)[-1] == "__main__":
        continue  # entry points run on import
    importlib.import_module(mod.name)
    seen += 1
assert seen > 50, seen
"""


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_runtime_dependencies_are_empty():
    assert _project()["dependencies"] == []


def test_github_and_discord_extras_exist():
    extras = _project()["optional-dependencies"]
    assert extras["github"] == ["cryptography>=42"]
    assert extras["discord"] == ["discord.py>=2.4"]


def test_every_module_imports_with_new_extras_blocked():
    proc = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK.format(blocked=BLOCKED)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
