"""d20 round 2: only a workflow whose definition digest is pinned in code may push.

This test recomputes the digest from the shipped file. If you changed
docs/rules/pr-fixer/workflows/pr-fixer.json on purpose, add the digest it prints to
culture_rules/actors/trusted.py (see that module for the rollout order).
"""

from __future__ import annotations

import copy
import re

from culture_rules.actors.trusted import (
    TRUSTED_WORKFLOW_DIGESTS,
    workflow_digest,
    workflow_refusal,
)
from tests.rules.test_pr_fixer_bundle import workflow_doc


def test_the_shipped_pr_fixer_workflow_is_trusted():
    digest = workflow_digest(workflow_doc())
    assert digest in TRUSTED_WORKFLOW_DIGESTS, (
        f"docs/rules/pr-fixer/workflows/pr-fixer.json now hashes to {digest}: if the change "
        "is intended, add it to TRUSTED_WORKFLOW_DIGESTS in culture_rules/actors/trusted.py"
    )


def test_the_set_holds_only_well_formed_digests():
    assert TRUSTED_WORKFLOW_DIGESTS
    assert all(re.fullmatch(r"sha256:[0-9a-f]{64}", d) for d in TRUSTED_WORKFLOW_DIGESTS)


def test_a_version_bump_keeps_trust_any_edit_loses_it():
    wf = workflow_doc()
    assert workflow_digest({**wf, "version": 9}) == workflow_digest(wf)
    edited = copy.deepcopy(wf)
    fix = next(s for s in edited["steps"] if s["id"] == "fix")
    next(b for b in fix["body"] if b["id"] == "gate")["placement"] = {
        "actor": "x",
        "machine": None,
        "requirement": None,
    }
    assert workflow_digest(edited) not in TRUSTED_WORKFLOW_DIGESTS
    for change in ({"description": "tweaked"}, {"steps": wf["steps"][:-1]}):
        assert workflow_digest({**wf, **change}) not in TRUSTED_WORKFLOW_DIGESTS


def test_workflow_refusal_reads_the_pinned_definition():
    wf = workflow_doc()
    assert workflow_refusal({"workflow": {"definition": wf, "digest": "sha256:forged"}}) is None
    edited = {**wf, "description": "x"}
    run = {
        "workflow": {"definition": edited, "digest": next(iter(TRUSTED_WORKFLOW_DIGESTS))}
    }  # a forged stored digest
    assert workflow_refusal(run) == "workflow_not_trusted"
    for run in (None, {}, {"workflow": None}, {"workflow": {"definition": "nope"}}):
        assert workflow_refusal(run) == "workflow_not_trusted"
