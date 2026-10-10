"""d37: the one rule the gate and the push share for which base may stand for a fix."""

from __future__ import annotations

import pytest

from culture_rules.actors.lineage import base_refusal, dispatched_base_tip
from culture_rules.store.memory import MemoryStore
from tests.actors.test_gate_base import dispatched_run

PULL, TIP, MID = "a" * 40, "b" * 40, "c" * 40


@pytest.mark.parametrize(
    ("base", "tip", "on_branch", "want"),
    [
        (PULL, None, None, None),  # the PR's own base.sha: as before
        (PULL, TIP, False, None),
        (TIP, TIP, True, None),  # the dispatched tip, still on the branch
        (TIP, TIP, False, "base_mismatch"),  # the dispatched tip, no longer on it
        (TIP, TIP, None, "base_unverified"),  # GitHub could not say
        (MID, TIP, True, "base_mismatch"),  # on the branch, but not what was dispatched
        (TIP, None, True, "base_mismatch"),  # no dispatched tip to vouch for it
    ],
)
def test_base_refusal(base, tip, on_branch, want):
    assert base_refusal(base, PULL, tip, on_branch) == want


def test_the_dispatched_tip_comes_only_from_a_genuine_dispatch_event():
    store = MemoryStore()
    run_id = dispatched_run(store, TIP)
    run = store.get("runs", run_id)
    assert dispatched_base_tip(store, run) == TIP
    forged = {**run, "id": "run-other"}  # not the run its rule's firing would start
    assert dispatched_base_tip(store, forged) is None
    assert dispatched_base_tip(store, {"id": "x", "rule_id": "r", "trigger": {}}) is None
    assert dispatched_base_tip(store, None) is None
