"""c17/h14 t12: every committed docs/rules workflow file is already migrated to ``notes``.

``migrate_notes`` moves each node's plain-text ``description`` into ``notes``. The committed
workflow files were migrated once with it (the old text now lives in ``notes``), so migrating
them again must change nothing, every ``description`` must be empty, and each file must be in
the canonical JSON export form. The old-to-new equality itself is checked once at migration
time (``git show <old>:<file>`` through ``migrate_notes`` equals the new file).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from culture_rules.io import codec
from culture_rules.model.migrate_notes import migrate_notes
from tests.rules.pre_migration import nodes

ROOT = Path(__file__).resolve().parents[2]
FILES = sorted((ROOT / "docs" / "rules").glob("*/workflows/*.json"))


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_committed_workflow_files_are_found():
    assert len(FILES) == 11


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_committed_workflow_is_already_migrated(path: Path):
    definition = _load(path)
    assert migrate_notes(definition) == definition


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_every_description_is_empty(path: Path):
    for node in nodes(_load(path)):
        assert node.get("description", "") == "", node.get("id", "workflow")


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_committed_workflow_is_in_canonical_export_form(path: Path):
    assert codec.dumps(_load(path), "json") == path.read_text(encoding="utf-8")
