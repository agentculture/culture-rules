"""The runtime stays dependency-free; github/discord extras import lazily (spec c25, c33)."""

import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BLOCKED = ("jwt", "cryptography", "discord")

IMPORT_CHECK = """
import sys
for name in {blocked!r}:
    sys.modules[name] = None
import culture_rules, culture_rules.server
import culture_rules.node, culture_rules.node.daemon, culture_rules.node.actors
import culture_rules.node.runner, culture_rules.node.firing, culture_rules.node.chain
import culture_rules.node.completions
"""


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def test_runtime_dependencies_are_empty():
    assert _project()["dependencies"] == []


def test_github_and_discord_extras_exist():
    extras = _project()["optional-dependencies"]
    assert extras["github"] == ["cryptography>=42"]
    assert extras["discord"] == ["discord.py>=2.4"]


def test_core_and_node_import_with_new_extras_blocked():
    proc = subprocess.run(
        [sys.executable, "-c", IMPORT_CHECK.format(blocked=BLOCKED)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
