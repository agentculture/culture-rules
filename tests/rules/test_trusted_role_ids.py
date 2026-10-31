"""Every trusted workflow's id is its role's name (spec c32, fold deviation d6).

The engine trusts by digest only, but the digest covers ``id``, and the web editor warns
before saving a workflow whose id is a trusted role (``web/src/workflows/trusted.ts``). That
match by id is only sound while each role's trusted definition carries ``id == role``; this
pins the convention so a new role named differently fails here, not silently in the editor.
"""

from __future__ import annotations

import json
from pathlib import Path

from culture_rules.actors import trusted

ROOT = Path(__file__).resolve().parents[2]
SOURCES = {
    trusted.ROLE_SINGLE: ROOT / "tests/rules/fixtures/pr-fixer-single/workflows/pr-fixer.json",
    trusted.ROLE_FIX: ROOT / "docs/rules/pr-fixer/workflows/pr-fix.json",
    trusted.ROLE_REVIEW: ROOT / "docs/rules/pr-fixer/workflows/review-commit.json",
    trusted.ROLE_PUBLISH: ROOT / "docs/rules/pr-fixer/workflows/publish-fix.json",
}


LIVE_ONLY = {trusted.ROLE_MERGE}
"""Roles whose workflow is live configuration with no committed source (the merge prompt)."""


def test_every_role_has_a_trusted_source():
    assert set(SOURCES) == set(trusted.TRUSTED_WORKFLOWS) - LIVE_ONLY


def test_each_trusted_definition_is_stored_under_its_role_name():
    """t12: each source is trusted in its role (v1 or v2), and its id is the role's name."""
    for role, path in SOURCES.items():
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["id"] == role, path
        assert trusted.workflow_role({"workflow": {"definition": doc}}) == role, path
        if "docs/rules" in str(path):  # committed definitions: v2 pins, prose left out
            assert trusted.workflow_digest_v2(doc) in trusted.TRUSTED_WORKFLOWS_V2[role], path
