"""R1 (c7): the generic modules import no PR-fixer module, not even lazily inside a function."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "culture_rules"

GENERIC = (
    "actors/agent.py",
    "node/firing.py",
    "node/daemon.py",
    "node/actions/github.py",
    "server/hooks/github.py",
)

FIXER_MODULES = frozenset(
    {
        "culture_rules.actors.gate",
        "culture_rules.actors.review",
        "culture_rules.actors.handoff",
        "culture_rules.actors.lineage",
        "culture_rules.actors.merge_hint",
        "culture_rules.node.actions.fixer_cooldown",
        "culture_rules.node.actions.fixer_levels",
        "culture_rules.node.actions.sonar",
        "culture_rules.node.actions.gitguardian",
        "culture_rules.node.actions.queue",
        "culture_rules.node.actions.github_pr",
        "culture_rules.node.story_stop",
        "culture_rules.node.conflict_watch",
        "culture_rules.node.reaction_watch",
        "culture_rules.node.status_board",
        "culture_rules.node.fixer_status",
    }
)


def _package(rel: str) -> str:
    return "culture_rules." + rel.removesuffix(".py").replace("/", ".")


def _imported(rel: str) -> set[str]:
    """Every module the file imports, anywhere in it, with relative imports resolved."""
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    parent = _package(rel).rsplit(".", 1)[0].split(".")
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                anchor = parent[: len(parent) - (node.level - 1)]
                base = ".".join(anchor + ([base] if base else []))
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("rel", GENERIC)
def test_generic_module_imports_no_fixer_module(rel: str) -> None:
    assert sorted(_imported(rel) & FIXER_MODULES) == []
