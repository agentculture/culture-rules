"""Executor.start_workflow: direct workflow runs pin a synthetic ``adhoc:<id>`` rule (t9).

Inputs are validated against the workflow's declared typed ports before anything is
written: a missing required input, a wrongly typed one, or an input the workflow does not
declare is refused with ``RunError`` code ``invalid_inputs`` naming the port. Every new run
document (direct or rule-started) carries top-level ``rule_id`` and ``workflow_id``.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.runs import (
    ACTION_STEP,
    ACTIVE,
    RUNS_COLLECTION,
    Containment,
    Executor,
    RunError,
)
from culture_rules.model.workflow import Output
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, ports_for, rule, step, workflow

PORT_TYPES_OK = {
    "string": "hello",
    "number": 1.5,
    "integer": 3,
    "boolean": True,
    "object": {"k": 1},
    "array": [1, 2],
}


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(clock) -> MemoryStore:
    return MemoryStore(clock=clock)


@pytest.fixture
def actor() -> FakeActor:
    return FakeActor(default=lambda inp, ctx: {"echo": inp.get("text", "")})


def make_executor(store, actor, clock) -> Executor:
    return Executor(store, "spark", ports_for(actor), clock=clock)


def typed_workflow(**kw):
    return workflow(
        (
            step(
                "a",
                inputs=(port("text", "string"), port("count", "integer", required=False)),
                outputs=(port("echo", "string"),),
            ),
        ),
        (edge("inputs", "text", "a", "text"), edge("inputs", "count", "a", "count")),
        inputs=(port("text", "string"), port("count", "integer", required=False)),
        outputs=(Output(name="echo", type="string", source="steps.a.outputs.echo"),),
        **kw,
    )


def put_workflow(store, wf, **extra):
    store.put("workflows", {"id": wf.id, **wf.to_dict(), **extra})


# ---------------------------------------------------------------- happy path


def test_matching_inputs_create_active_run_pinning_adhoc_rule(store, actor, clock):
    put_workflow(store, typed_workflow())
    ex = make_executor(store, actor, clock)
    run = ex.start_workflow("wf", {"text": "hi", "count": 2}, "alice")
    assert run["status"] == ACTIVE
    assert run["rule"]["id"].startswith("adhoc:")
    assert run["rule"]["id"] == "adhoc:wf"
    definition = run["rule"]["definition"]
    assert definition["trigger"]["kind"] == "manual"
    assert definition["action"]["kind"] == "noop"
    assert definition["workflow"]["inputs"] == {
        "text": {"$literal": "hi"},
        "count": {"$literal": 2},
    }
    assert run["workflow"]["id"] == "wf"
    assert run["inputs"] == {"text": "hi", "count": 2}
    assert run["started_by"] == "alice"
    assert ex.run(run["id"])["status"] == ACTIVE


def test_optional_port_may_be_omitted(store, actor, clock):
    put_workflow(store, typed_workflow())
    run = make_executor(store, actor, clock).start_workflow("wf", {"text": "hi"}, "alice")
    assert run["inputs"] == {"text": "hi"}


def test_explicit_null_for_optional_port_is_treated_as_absent(store, actor, clock):
    put_workflow(store, typed_workflow())
    run = make_executor(store, actor, clock).start_workflow(
        "wf", {"text": "hi", "count": None}, "alice"
    )
    assert run["inputs"] == {"text": "hi"}


def test_workflow_without_inputs_accepts_none(store, actor, clock):
    put_workflow(store, workflow((step("a"),)))
    run = make_executor(store, actor, clock).start_workflow("wf", None, "alice")
    assert run["inputs"] == {}
    assert run["rule"]["definition"]["workflow"]["inputs"] == {}


@pytest.mark.parametrize("ptype,value", sorted(PORT_TYPES_OK.items()))
def test_every_port_type_accepts_its_values(store, actor, clock, ptype, value):
    put_workflow(store, workflow((step("a"),), inputs=(port("x", ptype),)))
    run = make_executor(store, actor, clock).start_workflow("wf", {"x": value}, "alice")
    assert run["inputs"] == {"x": value}


def test_integer_value_is_a_valid_number(store, actor, clock):
    put_workflow(store, workflow((step("a"),), inputs=(port("x", "number"),)))
    run = make_executor(store, actor, clock).start_workflow("wf", {"x": 4}, "alice")
    assert run["inputs"] == {"x": 4}


def test_direct_run_executes_to_completion(store, actor, clock):
    put_workflow(store, typed_workflow())
    ex = make_executor(store, actor, clock)
    run = ex.start_workflow("wf", {"text": "hello"}, "alice")
    ex.run_until_idle()
    done = ex.run(run["id"])
    assert done["status"] == "succeeded"
    assert done["outputs"] == {"echo": "hello"}
    assert len(actor.calls_for(ACTION_STEP)) == 1
    assert actor.calls_for("a")[0][1] == {"text": "hello"}


def test_start_workflow_is_audited_with_caller_identity(store, actor, clock):
    put_workflow(store, typed_workflow())
    run = make_executor(store, actor, clock).start_workflow("wf", {"text": "x"}, "alice")
    audit = [d for d in store.find("audit", {}) if d["target"]["id"] == run["id"]]
    assert audit and audit[0]["identity"] == "alice"
    assert audit[0]["verb"] == "runs.start"


# ---------------------------------------------------------------- refusals


def _refused(store, actor, clock, inputs, wf=None):
    put_workflow(store, wf or typed_workflow())
    head = store.head(RUNS_COLLECTION)
    with pytest.raises(RunError) as exc:
        make_executor(store, actor, clock).start_workflow("wf", inputs, "alice")
    assert list(store.changes(RUNS_COLLECTION, head)) == []
    return exc.value


def test_missing_required_input_is_refused_naming_the_port(store, actor, clock):
    err = _refused(store, actor, clock, {"count": 1})
    assert err.code == "invalid_inputs"
    assert "text" in err.message
    assert err.details == [{"port": "text", "code": "input_missing"}]


@pytest.mark.parametrize(
    "ptype,bad",
    [
        ("string", 5),
        ("number", "5"),
        ("number", True),
        ("integer", 1.5),
        ("integer", True),
        ("integer", "3"),
        ("boolean", 1),
        ("boolean", "true"),
        ("object", [1]),
        ("array", {"a": 1}),
    ],
)
def test_wrong_type_is_refused_naming_the_port(store, actor, clock, ptype, bad):
    wf = workflow((step("a"),), inputs=(port("pval", ptype),))
    err = _refused(store, actor, clock, {"pval": bad}, wf)
    assert err.code == "invalid_inputs"
    assert "pval" in err.message
    assert ptype in err.message


def test_unknown_extra_input_is_refused_naming_it(store, actor, clock):
    err = _refused(store, actor, clock, {"text": "hi", "bogus": 1})
    assert err.code == "invalid_inputs"
    assert "bogus" in err.message


def test_non_mapping_inputs_are_refused(store, actor, clock):
    err = _refused(store, actor, clock, ["text"])
    assert err.code == "invalid_inputs"


def test_paused_engine_refuses(store, actor, clock):
    put_workflow(store, typed_workflow())
    Containment(store, clock=clock).pause("alice")
    with pytest.raises(RunError) as exc:
        make_executor(store, actor, clock).start_workflow("wf", {"text": "hi"}, "alice")
    assert exc.value.code == "paused"


def test_unknown_workflow_is_not_found(store, actor, clock):
    with pytest.raises(RunError) as exc:
        make_executor(store, actor, clock).start_workflow("ghost", {}, "alice")
    assert exc.value.code == "workflow_not_found"


def test_deleted_workflow_is_not_fireable(store, actor, clock):
    put_workflow(store, typed_workflow(), deleted_at="2026-01-01T00:00:00+00:00")
    with pytest.raises(RunError) as exc:
        make_executor(store, actor, clock).start_workflow("wf", {"text": "hi"}, "alice")
    assert exc.value.code == "not_fireable"


def test_disabled_workflow_is_not_fireable(store, actor, clock):
    wf = typed_workflow()
    store.put("workflows", {"id": wf.id, **wf.to_dict(), "enabled": False})
    with pytest.raises(RunError) as exc:
        make_executor(store, actor, clock).start_workflow("wf", {"text": "hi"}, "alice")
    assert exc.value.code == "not_fireable"


def test_invalid_inputs_maps_to_422():
    pytest.importorskip("fastapi")
    from culture_rules.server.app import _run_status

    assert _run_status("invalid_inputs") == 422


# ---------------------------------------------------------------- top-level ids


def test_direct_run_doc_has_top_level_ids(store, actor, clock):
    put_workflow(store, typed_workflow())
    ex = make_executor(store, actor, clock)
    run = ex.start_workflow("wf", {"text": "hi"}, "alice")
    stored = ex.run(run["id"])
    assert stored["rule_id"] == "adhoc:wf"
    assert stored["workflow_id"] == "wf"


def test_rule_started_run_doc_has_top_level_ids(store, actor, clock):
    ex = make_executor(store, actor, clock)
    run = ex.start(rule(), workflow((step("a"),)))
    assert ex.run(run["id"])["rule_id"] == "r1"
    assert ex.run(run["id"])["workflow_id"] == "wf"


def test_rule_without_workflow_has_null_workflow_id(store, actor, clock):
    ex = make_executor(store, actor, clock)
    run = ex.start(rule(workflow_id=None))
    stored = ex.run(run["id"])
    assert stored["rule_id"] == "r1"
    assert "workflow_id" in stored and stored["workflow_id"] is None
