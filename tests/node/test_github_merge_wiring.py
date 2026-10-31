"""green-pr-merge-prompt t2: the node registers github.merge (MissingExtraPort without the
github extra)."""

from __future__ import annotations

import importlib.util

import pytest

from culture_rules.node.runner import MissingExtraPort, default_ports
from culture_rules.store.memory import MemoryStore


def test_the_node_registers_github_merge():
    pytest.importorskip("cryptography")
    from culture_rules.node.actions.github_merge import GitHubMergePort

    ports = default_ports(MemoryStore(), "spark")
    assert isinstance(ports["action:github.merge"], GitHubMergePort)


def test_without_the_github_extra_github_merge_is_a_missing_extra_port(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "cryptography" else real(name, *a, **k),
    )
    ports = default_ports(MemoryStore(), "spark")
    assert isinstance(ports["action:github.merge"], MissingExtraPort)
