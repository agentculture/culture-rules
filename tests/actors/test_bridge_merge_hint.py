"""d36: a fixer try is told, at the bridge call, that it may make one real merge of the
run's base commit (/code-review #2, #3 and #8 on #46).

The paragraph is appended to the bridge payload's instruction the way the d26 status-notes
hint is: only for a run whose rule writes a status comment (the fixer's), only when the run
was given a ``base_sha``, never to a locked brief, and never when the instruction already
names that base commit (the conflict rule's, or a /fix that orders that merge). Nothing is
stored, so a retry's task cannot carry it twice; a missing instruction still falls back to
the task.
"""

from __future__ import annotations

import pytest

from culture_rules.actors.merge_hint import MERGE_PARAGRAPH_HEAD, merge_paragraph
from culture_rules.engine.actorport import InvocationContext
from culture_rules.node.fixer_status import STATUS_NOTE_HINT
from culture_rules.store.memory import MemoryStore
from tests.actors.test_bridge_agent import PR, FakeBridge, executor, make_actor, pr_wf
from tests.actors.test_bridge_status_notes import status_rule
from tests.engine.run_helpers import Clock, rule

BASE = "89abcdef0123456789abcdef0123456789abcdef"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


def sent(store, clock, the_rule, base=BASE, **actor_kw) -> tuple[str, dict]:
    """Run ``the_rule`` with ``base`` as the run's ``base_sha``; the bridge payload's
    instruction and the stored run."""
    bridge = FakeBridge()
    ex = executor(store, clock, make_actor(store, clock, bridge, **actor_kw))
    run = ex.start(the_rule, pr_wf(timeout_s=3600))
    doc = store.get("runs", run["id"])
    if base is not None:
        store.put("runs", {**doc, "inputs": {**(doc.get("inputs") or {}), "base_sha": base}})
    ex.run_until_idle()
    return bridge.requests[0]["body"]["input"]["instruction"], store.get("runs", run["id"])


def test_a_fixer_try_is_told_the_one_merge_once_before_the_status_hint(store, clock):
    instruction, run = sent(store, clock, status_rule())
    assert instruction == PR["instruction"] + merge_paragraph(BASE) + STATUS_NOTE_HINT
    assert instruction.count(MERGE_PARAGRAPH_HEAD) == 1
    assert f"git merge {BASE}" in instruction
    assert MERGE_PARAGRAPH_HEAD not in str(run.get("inputs"))  # never stored


def test_a_run_that_writes_no_status_comment_is_not_a_fixer_try(store, clock):
    instruction, _ = sent(store, clock, rule())
    assert instruction == PR["instruction"]


def test_a_run_without_a_base_commit_gets_no_paragraph(store, clock):
    instruction, _ = sent(store, clock, status_rule(), base=None)
    assert instruction == PR["instruction"] + STATUS_NOTE_HINT


@pytest.mark.parametrize("base", ["main", "89abcdef", "", 7])
def test_only_a_full_commit_sha_is_named(store, clock, base):
    instruction, _ = sent(store, clock, status_rule(), base=base)
    assert MERGE_PARAGRAPH_HEAD not in instruction


def context(run_id: str) -> InvocationContext:
    return InvocationContext(run_id=run_id, step_id="fix", kind="actor_task", host="spark")


def test_an_instruction_that_names_the_base_already_gets_none(store, clock):
    """/code-review #8: the conflict rule (and a /fix ordering that merge) already says
    'merge exactly the base commit ...' - a second, conditional paragraph would weaken it."""
    instruction, _ = sent(store, clock, status_rule())
    assert MERGE_PARAGRAPH_HEAD in instruction  # the plain case, for contrast
    actor = make_actor(store, clock)
    store.put("runs", {**store.find("runs")[0], "id": "run-x"})
    ordered = f"Merge exactly the base commit {BASE} (git merge {BASE}); make no other change."
    told = actor._with_merge_hint({"instruction": ordered}, context("run-x"))
    assert told["instruction"] == ordered


def test_a_locked_brief_is_never_changed(store, clock):
    sent(store, clock, status_rule())  # a fixer run with a base commit in the store
    actor = make_actor(store, clock, defaults={"locked_instruction": "pr-fixer-review"})
    run_id = store.find("runs")[0]["id"]
    payload = {"instruction": "the reviewer's fixed brief"}
    assert actor._with_merge_hint(payload, context(run_id)) == payload


def test_a_missing_instruction_still_falls_back_to_the_task(store, clock):
    """/code-review #2: with the paragraph appended at the bridge (not in the dispatch rule's
    instruction template), a request without an instruction still reaches the agent as its
    task, followed by the paragraph."""
    sent(store, clock, status_rule())
    actor = make_actor(store, clock)
    run_id = store.find("runs")[0]["id"]
    payload, problem = actor.bridge_input({**PR, "instruction": None, "task": "Do the task"}, {})
    assert problem is None
    told = actor._with_merge_hint(payload, context(run_id))
    assert told["instruction"] == "Do the task" + merge_paragraph(BASE)
