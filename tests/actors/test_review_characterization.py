"""Characterization tests (Sonar S3776 refactor of review.py): pin record_review's race and
fail-closed paths and the early refusals of the verdict step's ``_review`` exactly as they
behave, so the split into helpers is provably behaviour-preserving."""

from __future__ import annotations

import pytest

from culture_rules.actors.review import (
    CURRENT_COLLECTION,
    REVIEWS_COLLECTION,
    ReviewError,
    ReviewVerdictPort,
    record_review,
)
from culture_rules.store.memory import MemoryStore

SHA, START = "a" * 40, "c" * 40


class _Lost:
    won = False


def _fields(**over):
    fields = {"step": "fix[0]/verdict", "verdict": "approve", "commit_sha": SHA}
    fields.update(over)
    return fields


# --------------------------------------------------------------------------- record_review


def test_a_pointer_insert_race_retries_and_moves_the_pointer_forward():
    store = MemoryStore()
    record_review(store, "run-1", iteration=0, attempt=1, fields=_fields())
    real_get = store.get
    misses = []

    def stale(collection, id):
        if collection == CURRENT_COLLECTION and not misses:
            misses.append(id)
            return None
        return real_get(collection, id)

    store.get = stale
    rid = record_review(store, "run-1", iteration=1, attempt=1, fields=_fields(step="s1"))
    store.get = real_get
    assert misses == ["run-1"]
    cur = store.get(CURRENT_COLLECTION, "run-1")
    assert (cur["record"], cur["state"], cur["iteration"], cur["attempt"]) == (rid, "current", 1, 1)


def test_a_clash_before_any_pointer_is_a_conflict_pointer():
    store = MemoryStore()
    rid = "run-1:fix[0]/verdict:1"
    store.insert(
        REVIEWS_COLLECTION,
        {**_fields(verdict="request_changes"), "id": rid, "run_id": "run-1"},
    )
    assert record_review(store, "run-1", iteration=0, attempt=1, fields=_fields()) == rid
    cur = store.get(CURRENT_COLLECTION, "run-1")
    assert cur["state"] == "conflict"
    assert cur["record"] is None
    assert cur["conflict"] == [rid, rid]
    assert store.get(REVIEWS_COLLECTION, rid)["verdict"] == "request_changes"  # first stands


def test_an_identical_rewrite_is_no_clash():
    store = MemoryStore()
    rid = record_review(store, "run-1", iteration=0, attempt=1, fields=_fields())
    assert record_review(store, "run-1", iteration=0, attempt=1, fields=_fields()) == rid
    assert store.get(CURRENT_COLLECTION, "run-1")["state"] == "current"


def test_another_record_for_the_same_try_is_a_conflict():
    store = MemoryStore()
    first = record_review(store, "run-1", iteration=0, attempt=1, fields=_fields())
    other = record_review(
        store, "run-1", iteration=0, attempt=1, fields=_fields(step="fix[0]/verdict2")
    )
    cur = store.get(CURRENT_COLLECTION, "run-1")
    assert (cur["state"], cur["record"], cur["conflict"]) == ("conflict", None, [first, other])


def test_a_clash_on_an_older_try_leaves_a_newer_pointer_alone():
    store = MemoryStore()
    old = record_review(store, "run-1", iteration=0, attempt=1, fields=_fields())
    new = record_review(store, "run-1", iteration=2, attempt=1, fields=_fields(step="s2"))
    assert (
        record_review(
            store, "run-1", iteration=0, attempt=1, fields=_fields(verdict="request_changes")
        )
        == old
    )
    cur = store.get(CURRENT_COLLECTION, "run-1")
    assert (cur["state"], cur["record"]) == ("current", new)


def test_a_consumed_pointer_refuses_and_an_unknown_state_is_kept():
    store = MemoryStore()
    record_review(store, "run-1", iteration=0, attempt=1, fields=_fields())
    store.update_if(CURRENT_COLLECTION, "run-1", {}, {"state": "consumed"})
    with pytest.raises(ReviewError) as exc:
        record_review(store, "run-1", iteration=3, attempt=1, fields=_fields())
    assert exc.value.code == "review_consumed"
    assert str(exc.value) == (
        "review_consumed: a push already used this run's approval; recorded only"
    )
    assert store.get(REVIEWS_COLLECTION, "run-1:fix[0]/verdict:1") is not None  # kept
    store.update_if(CURRENT_COLLECTION, "run-1", {}, {"state": "weird"})
    before = store.get(CURRENT_COLLECTION, "run-1")
    rid = record_review(store, "run-1", iteration=4, attempt=1, fields=_fields(step="s4"))
    assert rid == "run-1:s4:1"
    assert store.get(CURRENT_COLLECTION, "run-1") == before


def test_a_lost_pointer_cas_rereads_then_wins():
    store = MemoryStore()
    record_review(store, "run-1", iteration=0, attempt=1, fields=_fields())
    real = store.update_if
    lost = []

    def lose_once(collection, id, expected, changes, **kw):
        if collection == CURRENT_COLLECTION and not lost:
            lost.append((dict(expected), dict(changes)))
            return _Lost()
        return real(collection, id, expected, changes, **kw)

    store.update_if = lose_once
    rid = record_review(store, "run-1", iteration=1, attempt=2, fields=_fields(step="s"))
    store.update_if = real
    assert lost == [
        (
            {
                "record": "run-1:fix[0]/verdict:1",
                "iteration": 0,
                "attempt": 1,
                "state": "current",
            },
            {"record": rid, "iteration": 1, "attempt": 2},
        )
    ]
    assert store.get(CURRENT_COLLECTION, "run-1")["record"] == rid


def test_sustained_pointer_contention_raises_review_invalid():
    store = MemoryStore()
    record_review(store, "run-1", iteration=0, attempt=1, fields=_fields())
    calls = []

    def always_lose(collection, id, expected, changes, **kw):
        calls.append(collection)
        return _Lost()

    store.update_if = always_lose
    with pytest.raises(ReviewError) as exc:
        record_review(store, "run-1", iteration=1, attempt=1, fields=_fields())
    assert exc.value.code == "review_invalid"
    assert "sustained contention" in str(exc.value)
    assert calls == [CURRENT_COLLECTION] * 16


def test_the_record_id_defaults_its_step_to_the_iteration():
    store = MemoryStore()
    rid = record_review(store, "run-1", iteration=4, attempt=2, fields={"verdict": "approve"})
    assert rid == "run-1:[4]:2"


# --------------------------------------------------------------------------- _review refusals

NAMES = {"gate": "gate", "review": "review"}
G = {"commit_sha": SHA, "start_sha": START, "diff": "+x", "agent_commit_sha": SHA}
GIVEN = {"commit_sha": SHA, "head_sha": START, "diff": "+x", "diff_truncated": False}


def _run(gate_def=None, review_def=None):
    body = [
        gate_def or {"id": "gate", "kind": "code", "config": {"builtin": "gate"}},
        review_def or {"id": "review", "kind": "ai", "placement": {"actor": "rev"}},
        {"id": "fix", "kind": "ai", "placement": {"actor": "impl"}},
    ]
    return {
        "id": "run-1",
        "workflow": {"definition": {"steps": [{"id": "loop", "body": body}]}},
        "steps": [
            {
                "key": "loop[0]/fix",
                "status": "succeeded",
                "outputs": {"head_after": SHA, "backend": "qwen"},
            }
        ],
    }


def _review_state(**over):
    st = {"status": "succeeded", "inputs": dict(GIVEN), "outputs": {}}
    st.update(over)
    return st


def _code(store, run, review, g=G):
    port = ReviewVerdictPort(store)
    with pytest.raises(ReviewError) as exc:
        port._review(run, "loop", 0, NAMES, lambda role: review, g, {})
    return exc.value.code, exc.value.detail


@pytest.mark.parametrize("review", [None, {}, _review_state(status="failed")])
def test_no_successful_review_is_review_missing(review):
    assert _code(MemoryStore(), _run(), review) == (
        "review_missing",
        "the reviewer did not review this commit",
    )


@pytest.mark.parametrize(
    "given",
    [
        {**GIVEN, "commit_sha": "b" * 40},
        {**GIVEN, "head_sha": SHA},
        {**GIVEN, "diff": "+y"},
        {**GIVEN, "diff_truncated": None},
        {},
    ],
)
def test_a_reviewer_not_given_the_gates_commit_and_diff_is_review_invalid(given):
    assert _code(MemoryStore(), _run(), _review_state(inputs=given)) == (
        "review_invalid",
        "the reviewer was not given the gate's commit and diff",
    )


@pytest.mark.parametrize(
    "gate_def, review_def",
    [
        ({"id": "gate", "kind": "ai", "config": {"builtin": "gate"}}, None),
        ({"id": "gate", "kind": "code", "config": {"builtin": "lint"}}, None),
        ({"id": "gate", "kind": "code"}, None),
        (
            {
                "id": "gate",
                "kind": "code",
                "config": {"builtin": "gate"},
                "placement": {"actor": "x"},
            },
            None,
        ),
        (None, {"id": "review", "kind": "code", "placement": {"actor": "rev"}}),
    ],
)
def test_a_gate_that_is_not_the_builtin_or_a_review_that_is_not_ai_is_bad_config(
    gate_def, review_def
):
    assert _code(MemoryStore(), _run(gate_def, review_def), _review_state()) == (
        "bad_config",
        "gate_step must be the built-in gate, review_step an ai step",
    )


def _actors(store, reviewer_params=None, reviewer_harness="codex"):
    params = {"sandbox": "read-only", "reviewer": True}
    params.update(reviewer_params or {})
    store.put("actors", {"id": "rev", "params": params, "harness": reviewer_harness})
    store.put("actors", {"id": "impl", "params": {}, "harness": "qwen"})


def test_a_non_mapping_gate_placement_is_not_an_actor_placement():
    store = MemoryStore()
    _actors(store, {"sandbox": "workspace-write"})
    gate = {"id": "gate", "kind": "code", "config": {"builtin": "gate"}, "placement": "x"}
    assert _code(store, _run(gate), _review_state())[0] == "reviewer_not_read_only"


@pytest.mark.parametrize(
    "params, review_config",
    [
        ({"sandbox": "workspace-write"}, None),
        ({"sandbox": None}, None),
        ({}, {"sandbox": "workspace-write"}),
    ],
)
def test_a_reviewer_that_is_not_read_only_is_refused(params, review_config):
    store = MemoryStore()
    _actors(store, params)
    review_def = {"id": "review", "kind": "ai", "placement": {"actor": "rev"}}
    if review_config is not None:
        review_def["config"] = review_config
    assert _code(store, _run(review_def=review_def), _review_state()) == (
        "reviewer_not_read_only",
        "rev is not read-only",
    )


@pytest.mark.parametrize(
    "params, harness",
    [({"reviewer": False}, "codex"), ({"reviewer": "yes"}, "codex"), ({}, "claude")],
)
def test_a_reviewer_not_flagged_or_on_another_backend_is_not_allowed(params, harness):
    store = MemoryStore()
    _actors(store, params, harness)
    code, detail = _code(store, _run(), _review_state())
    assert code == "reviewer_not_allowed"
    assert detail == "rev is not an actor flagged as a reviewer with backend in ['codex']"


def test_a_reviewer_on_the_implementers_backend_is_the_implementer():
    store = MemoryStore()
    _actors(store)
    run = _run()
    run["steps"][0]["outputs"]["backend"] = "codex"
    store.put("actors", {"id": "impl", "params": {}, "harness": "codex"})
    assert _code(store, run, _review_state()) == (
        "reviewer_is_implementer",
        "reviewer rev/codex vs implementer impl/codex",
    )


def test_the_facts_gathered_before_a_refusal_are_kept():
    store = MemoryStore()
    _actors(store, {"reviewer": False})
    facts: dict = {}
    port = ReviewVerdictPort(store)
    with pytest.raises(ReviewError):
        port._review(_run(), "loop", 0, NAMES, lambda role: _review_state(), G, facts)
    assert facts == {"reviewer_actor": "rev", "implementer_actor": "impl"}
