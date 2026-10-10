"""d21: what ``github.push`` stands on - the verified chain behind a ``publish-fix`` run.

A real chain runs first (tests/rules/chain_world.py, push recorded, not performed); then the
push port's chain check (:func:`culture_rules.node.actions.github_pr._chain`) is asked about
the publish run's own push, and about every way it could be bent: other params, an edited
event, a run that was not its rule's firing, an untrusted or wrong-role upstream, a gate
that did not pass. Each is refused before any approval is even looked at.
"""

from __future__ import annotations

import copy

import pytest

from culture_rules.actors.lineage import LineageError
from culture_rules.engine.runs import RUNS_COLLECTION, step_state
from culture_rules.node.actions.github_pr import _chain
from tests.rules.chain_world import ChainWorld


@pytest.fixture
def chain(tmp_path):
    w = ChainWorld(tmp_path)
    w.fire()
    (fix,) = w.run_of("pr-fix")
    (review,) = w.run_of("review-commit")
    (publish,) = w.run_of("publish-fix")
    params = dict(step_state(publish, "push")["inputs"])
    return w, fix, review, publish, params


def refused(store, run, params) -> str:
    with pytest.raises(LineageError) as err:
        _chain(store, run, params)
    return err.value.code


def test_the_publish_runs_own_push_stands_on_its_chain(chain):
    w, fix, review, publish, params = chain
    got = _chain(w.c.base, publish, params)
    assert got.fix_run["id"] == fix["id"]
    assert got.reviewer_run == review["id"]
    # #35: the fix was dispatched by the queue; the chain reaches the run that queued it
    (queued,) = w.run_of("queue-add")
    assert queued["rule_id"] == "pr-fixer-checks"
    assert [r["id"] for r in got.runs] == [publish["id"], review["id"], fix["id"], queued["id"]]
    assert got.target is not None


def _edit_dispatch(w, fix, **data):
    event = w.c.base.get("events", fix["trigger"]["id"])
    env = copy.deepcopy(event["envelope"])
    env["data"].update(data)
    w.c.base.put("events", {**event, "envelope": env})


def test_a_dispatch_event_edited_in_the_store_is_unverified(chain):
    w, fix, _, publish, params = chain
    _edit_dispatch(w, fix, instruction="something else")
    assert refused(w.c.base, publish, params) == "chain_unverified"


def test_a_dispatch_naming_another_or_no_queue_add_run_is_unverified(chain):
    w, fix, review, publish, params = chain
    for source in (review["id"], "run-missing"):
        run = copy.deepcopy(fix)
        run["trigger"]["data"]["source_run"] = source
        w.c.base.put(RUNS_COLLECTION, run)
        _edit_dispatch(w, fix, source_run=source)
        assert refused(w.c.base, publish, params) == "chain_unverified"


def test_a_dispatch_not_from_the_queue_source_is_unverified(chain):
    w, fix, _, publish, params = chain
    event = w.c.base.get("events", fix["trigger"]["id"])
    env = {**event["envelope"], "source": "somewhere-else"}
    w.c.base.put("events", {**event, "envelope": env})
    run = copy.deepcopy(fix)
    run["trigger"] = copy.deepcopy(env)
    w.c.base.put(RUNS_COLLECTION, run)
    assert refused(w.c.base, publish, params) == "chain_unverified"


@pytest.mark.parametrize(
    "over",
    [
        {"commit_sha": "f" * 40},
        {"expected_head_sha": "e" * 40},
        {"source": "/tmp/elsewhere.bundle"},
        {"repo": "o/other"},
        {"number": 8},
        {"head_branch": "main"},
    ],
    ids=["commit", "start", "bundle", "repo", "number", "branch"],
)
def test_any_other_push_than_the_gated_commit_is_a_mismatch(chain, over):
    w, _fix, _review, publish, params = chain
    assert refused(w.c.base, publish, {**params, **over}) == "chain_mismatch"


def test_an_edited_trigger_event_is_unverified(chain):
    w, _fix, _review, publish, params = chain
    forged = copy.deepcopy(publish)
    forged["trigger"]["data"]["outputs"]["commit_sha"] = "f" * 40
    assert refused(w.c.base, forged, params) == "chain_unverified"


def test_a_run_that_is_not_its_rules_firing_is_unverified(chain):
    w, _fix, _review, publish, params = chain
    copied = {**copy.deepcopy(publish), "id": "run-handmade"}
    assert refused(w.c.base, copied, params) == "chain_unverified"


def test_a_publish_started_straight_from_a_fix_is_the_wrong_role(chain):
    w, fix, review, publish, params = chain
    # a publish whose trigger is the fix run's own event (no review in between)
    from culture_rules.node.firing import run_id_for

    event = copy.deepcopy(review["trigger"])  # the genuine rules.run.succeeded of pr-fix
    wrong = {
        **copy.deepcopy(publish),
        "trigger": event,
        "id": run_id_for(publish["rule_id"], event["id"]),
    }
    assert refused(w.c.base, wrong, params) == "workflow_not_trusted"


def test_an_untrusted_upstream_is_refused(chain):
    w, fix, _review, publish, params = chain
    edited = copy.deepcopy(fix)
    edited["workflow"]["definition"]["description"] = "tweaked"
    w.c.base.put(RUNS_COLLECTION, edited)
    assert refused(w.c.base, publish, params) == "workflow_not_trusted"


def test_a_gate_that_did_not_pass_is_refused_whatever_the_params_say(chain):
    w, fix, _review, publish, params = chain
    run = copy.deepcopy(fix)
    loop = step_state(run, "fix")
    gate = step_state(run, f"fix[{loop['iteration']}]/gate")
    gate["outputs"]["verdict"] = "no_gate"
    w.c.base.put(RUNS_COLLECTION, run)
    assert refused(w.c.base, publish, {**params, "gate_verdict": "pass"}) == "gate_not_passed"


def test_a_run_of_an_untrusted_workflow_cannot_push(chain):
    w, _fix, _review, publish, params = chain
    edited = copy.deepcopy(publish)
    edited["workflow"]["definition"]["description"] = "tweaked"
    assert refused(w.c.base, edited, params) == "workflow_not_trusted"
    assert refused(w.c.base, None, params) == "run_not_found"
