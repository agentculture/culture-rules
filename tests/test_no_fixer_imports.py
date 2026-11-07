"""R1 (c7): the generic modules import no PR-fixer module, not even lazily inside a function."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "culture_rules"

GENERIC = tuple(str(path.relative_to(ROOT)) for path in sorted(ROOT.rglob("*.py")))

FIXER_MODULES = frozenset(
    {
        "culture_rules.apps.sonarcloud",
        "culture_rules.apps.gitguardian",
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


def test_removed_modules_are_absent():
    for module in FIXER_MODULES:
        assert not (ROOT.parent / (module.replace(".", "/") + ".py")).exists()


def test_removed_ports_are_absent_and_select_stays_registered():
    from culture_rules.model.action_kinds import ACTION_KINDS
    from culture_rules.node.runner import default_ports
    from culture_rules.store.memory import MemoryStore

    ports = default_ports(MemoryStore(), "test")
    for kind in ("github.push", "github.review_reply"):
        assert kind not in ACTION_KINDS
        assert f"action:{kind}" not in ports
    assert "status" not in ACTION_KINDS["github.comment"].params
    assert "select" in ports["code"]._builtins
    assert not set(ports["code"]._builtins) & {
        "gate",
        "review",
        "github.threads",
        "github.threads_addressed",
        "sonar.gate_issues",
        "gitguardian.findings",
        "gitguardian.hold",
        "queue.add",
        "queue.progress",
        "queue.stop",
        "handoff.export",
        "handoff.import",
        "fixer.cooldown",
    }
