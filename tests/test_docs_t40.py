"""t40: README, demo script and the five prompt files describe the shipped four-tab design."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ["CLAUDE.md", "QWEN.md", "AGENTS.override.md", "AGENTS.colleague.md", ".pi/SYSTEM.md"]
WHY = (
    "One graphical, agent-operable place to decide when work happens, how it flows across "
    "machines and who does it"
)
STALE = [
    "scaffold only",
    "exactly three",
    "three primary tabs",
    "not yet implemented",
    "no rules engine",
    "visual editor with three tabs",
]
EXTRAS = ["server", "store", "mcp", "events", "yaml", "backup", "agent"]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", _read(rel))


def test_readme_names_both_audiences_and_the_why() -> None:
    text = _flat("README.md")
    assert "operator" in text.lower() and "mesh agents" in text.lower()
    assert WHY in text


def test_readme_four_tabs_mcp_and_no_stale_wording() -> None:
    text = _flat("README.md")
    assert "Rules | Workflows | Actors | Statistics" in text
    assert "MCP" in text
    for phrase in STALE:
        assert phrase not in text.lower(), phrase


def test_readme_documents_extras_surface_and_links() -> None:
    text = _read("README.md")
    for extra in EXTRAS:
        assert f"culture-rules[{extra}]" in text or f"[{extra}]" in text, extra
    for needle in [
        "culture-rules serve",
        "culture-rules node run",
        "culture-rules mcp",
        "docs/demo.md",
        "docs/operations/replica-set.md",
        "docs/operations/backup.md",
        "docs/operations/rules-culture-dev.md",
        "## Background",
    ]:
        assert needle in text, needle


def test_readme_background_cites_scope_entries() -> None:
    background = _read("README.md").split("## Background", 1)[1]
    for entry in [f"s{n}" for n in range(7, 16)] + ["s25"]:
        assert re.search(rf"\b{entry}\b", background), entry


def test_demo_reproduces_the_after_state() -> None:
    text = _read("docs/demo.md")
    for needle in [
        "culture-rules serve",
        "culture-rules machines create",
        "culture-rules actors create",
        "culture-rules workflows create",
        "culture-rules rules create",
        "culture-rules node run --once --host spark",
        "culture-rules runs show",
        "culture-rules rules replay",
        "culture-rules rules enable",
        "Statistics",
        "--apply",
    ]:
        assert needle in text, needle


@pytest.mark.parametrize("rel", PROMPTS)
def test_prompt_files_describe_the_four_tab_design(rel: str) -> None:
    text = _flat(rel)
    lowered = text.lower()
    assert "Statistics" in text, rel
    assert "Rules | Workflows | Actors | Statistics" in text, rel
    assert "mcp" in lowered, rel
    for phrase in STALE:
        assert phrase not in lowered, f"{phrase!r} in {rel}"


def test_load_bearing_phrases_survive() -> None:
    assert "culture-rules" in _read("CLAUDE.md") and "culture-rules" in _read("QWEN.md")
    assert "lobes-cli" in _read(".pi/SYSTEM.md")
    assert "lobes-cli" not in _read("AGENTS.override.md")
    assert not (ROOT / "AGENTS.md").exists()
