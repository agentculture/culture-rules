"""t10 (editor fold): docs and the harness prompt files describe the four tabs.

Spec docs/specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md
(c17, h10, c31, h21, c6, h1): the editor's primary tabs are Workflows |
Actors | Variables | Statistics; rules are shown as a workflow's entry points.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ["CLAUDE.md", "QWEN.md", "AGENTS.override.md", "AGENTS.colleague.md", ".pi/SYSTEM.md"]
# The prompt files that state the tab constraint as a rule (".pi/SYSTEM.md" only names the tabs).
CONSTRAINT_PROMPTS = ["CLAUDE.md", "QWEN.md", "AGENTS.override.md", "AGENTS.colleague.md"]
FOLD_DOCS = [
    "README.md",
    "web/README.md",
    "docs/demo.md",
    "docs/operations/pr-fixer.md",
    "docs/run-events.md",
]
TABS = "Workflows | Actors | Variables | Statistics"
OLD_TABS = "Rules | Workflows"
SPEC = "docs/specs/2026-10-03-culture-rules-engine-editor.md"


def _flat(rel: str) -> str:
    return re.sub(r"\s+", " ", (ROOT / rel).read_text(encoding="utf-8"))


@pytest.mark.parametrize("rel", PROMPTS)
def test_prompt_files_name_the_four_tabs_and_no_rules_tab(rel: str) -> None:
    text = _flat(rel)
    assert TABS in text, rel
    assert OLD_TABS not in text, rel
    assert "five tabs" not in text.lower(), rel
    assert "five primary tabs" not in text.lower(), rel
    assert "no Rules tab" in text, rel
    assert "entry point" in text, rel


@pytest.mark.parametrize("rel", CONSTRAINT_PROMPTS)
def test_prompt_files_state_exactly_four_primary_tabs(rel: str) -> None:
    assert f"Exactly four primary tabs: {TABS}." in _flat(rel), rel


def test_claude_md_intro_and_constraint_both_moved() -> None:
    text = _flat("CLAUDE.md")
    assert f"four tabs: **{TABS}**" in text
    assert f"Exactly four primary tabs: {TABS}." in text


def test_prompt_files_describe_the_three_views_in_step() -> None:
    for rel in CONSTRAINT_PROMPTS:
        text = _flat(rel)
        for view in ("Simple", "Detailed", "Debug"):
            assert view in text, f"{view} missing in {rel}"


def test_load_bearing_phrases_survive_the_fold() -> None:
    assert "culture-rules" in _flat("CLAUDE.md")
    assert "culture-rules" in _flat("QWEN.md")
    assert "lobes-cli" in _flat(".pi/SYSTEM.md")
    assert "lobes-cli" not in _flat("AGENTS.override.md")
    assert not (ROOT / "AGENTS.md").exists()


def test_engine_editor_spec_carries_a_dated_amendment_not_a_silent_edit() -> None:
    raw = (ROOT / SPEC).read_text(encoding="utf-8")
    # The original decision text stays as written.
    assert "Exactly five primary tabs: Rules | Workflows | Actors | Variables | Statistics." in raw
    head, sep, amendment = raw.partition("## Amendment 2026-10-09")
    assert sep, "no dated amendment section"
    amendment = re.sub(r"\s+", " ", amendment)
    assert TABS in amendment
    assert "no Rules tab" in amendment
    assert "docs/specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md" in amendment
    for view in ("Simple", "Detailed", "Debug"):
        assert view in amendment


@pytest.mark.parametrize("rel", FOLD_DOCS)
def test_fold_docs_describe_the_folded_editor(rel: str) -> None:
    text = _flat(rel)
    assert OLD_TABS not in text, rel
    assert "Rules tab" not in text or "no Rules tab" in text, rel
    assert "entry point" in text, rel


@pytest.mark.parametrize("rel", ["README.md", "web/README.md", "docs/demo.md"])
def test_editor_docs_name_the_four_tabs_and_three_views(rel: str) -> None:
    text = _flat(rel)
    assert TABS in text, rel
    for view in ("Simple", "Detailed", "Debug"):
        assert view in text, f"{view} missing in {rel}"


def test_web_readme_names_redirects_and_the_agent_state_alias() -> None:
    text = _flat("web/README.md")
    assert "legacy-redirects.ts" in text
    assert "/rules/<id>" in text
    assert "deprecated alias" in text
    for key in ("view", "entries", "entry", "chains", "without_workflow"):
        assert f"`{key}`" in text, key


def test_run_events_states_the_stepless_workflow_fact() -> None:
    text = _flat("docs/run-events.md")
    assert "no steps" in text
    assert "`data.workflow_id`" in text and "`data.workflow_version`" in text
    assert "tests/engine/test_stepless_workflow.py" in text


def test_pr_fixer_ops_doc_reads_the_fixer_as_one_chain() -> None:
    text = _flat("docs/operations/pr-fixer.md")
    assert "chain card" in text
    assert "Continues into" in text


def test_pr_fixer_ops_doc_explains_the_trusted_save_question() -> None:
    text = _flat("docs/operations/pr-fixer.md")
    assert "The editor does not warn" not in text
    for needle in [
        "Save a trusted workflow?",
        "Keep editing",
        "Save anyway",
        "web/src/workflows/trusted.ts",
        "tests/rules/test_trusted_role_ids.py",
        "culture_rules/actors/trusted.py",
        "a rename from the head",
        "runs still start",
        "Saving the original definition back restores the trust",
    ]:
        assert needle in text, needle


def test_web_readme_counts_two_chains_and_documents_the_trust_question() -> None:
    text = _flat("web/README.md")
    assert '"chains": 2' in text
    assert "Save a trusted workflow?" in text
    assert "rules unavailable" in text
    assert "liveFeed.ts" in text
    assert "RULES_SCREENSHOT=<path> sets where the Rules screenshot" not in text
