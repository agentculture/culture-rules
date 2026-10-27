"""culture-rules#97 t7 (d4): a ``for_each`` loop's ``outcomes`` output port.

A ``for_each`` loop that declares an output port named ``outcomes`` gets one entry per item,
in order: ``{index, item, status, errors}`` plus each ``config.collect`` name. ``status`` is
``succeeded`` when every body step succeeded and ``skipped`` when any body step was skipped
(a failed body step still fails the loop); ``errors`` maps each skipped body step that kept an
error (a failure ``on_error: "skip"`` tolerated, an ``input_missing`` behind it) to its
message. A skip without an error (a false ``when``, a disabled step) makes the iteration
``skipped`` and adds nothing to ``errors``. ``config.collect`` (``{name: "<body step>.<output>"}``)
copies a body step's output into the entry as is, ``None`` when the step did not output it.
"""

from __future__ import annotations

import pytest

from culture_rules.engine.runs import Executor, RunError, step_key, step_state
from culture_rules.model.validate import ValidationError, validate
from culture_rules.store.memory import MemoryStore
from tests.engine.run_helpers import Clock, FakeActor, edge, port, rule, step, workflow

SKIP = {"on_error": "skip"}
DOWN = ("fail", "fetch_failed: 404", False)
ITEMS = "items"
ARRAY = "array"
EACH = "each"
FETCH = "fetch"
COUNT = "count"
STATUS = "status"
ERRORS = "errors"
OUTCOMES = "outcomes"
SUCCEEDED = "succeeded"
SKIPPED = "skipped"
NEVER = {"op": "compare", "cmp": "==", "left": {"field": "path"}, "right": {"literal": "never"}}
CONFIG = "steps[1].config.collect"


@pytest.fixture
def store():
    return MemoryStore()


@pytest.fixture
def clock():
    return Clock()


def _body(fetch_config=None, count_config=None, count_enabled=True):
    """fetch (outputs path) -> count (needs path, outputs count)."""
    return (
        step(FETCH, inputs=(port("item"),), outputs=(port("path", "string"),), config=fetch_config),
        step(
            COUNT,
            inputs=(port("path", "string"),),
            outputs=(port(COUNT, "integer"), port("note", "string", required=False)),
            config=count_config if count_config is not None else dict(SKIP),
            enabled=count_enabled,
        ),
    )


def _loop(outputs=(port(OUTCOMES, ARRAY),), config=None, body=None, kind="for_each"):
    return step(
        EACH,
        kind,
        inputs=(port(ITEMS, ARRAY),),
        outputs=outputs,
        max_iterations=5,
        config={"items": ITEMS, **(config or {})},
        body=body if body is not None else _body(dict(SKIP)),
    )


def _wf(loop):
    return workflow(
        (step("list", outputs=(port(ITEMS, ARRAY),)), loop),
        (
            edge("list", ITEMS, EACH, ITEMS),
            edge(FETCH, "path", COUNT, "path"),
        ),
    )


def _actor(items, fail_at=()):
    """fetch answers `/f/<item>`, count its length; `fail_at` iterations' fetch fails."""
    a = FakeActor()
    a.on("list", ("complete", {ITEMS: items}))
    for i, item in enumerate(items):
        if i in fail_at:
            a.on(step_key(EACH, i, FETCH), DOWN)
        else:
            a.on(step_key(EACH, i, FETCH), ("complete", {"path": f"/f/{item}"}))
            a.on(step_key(EACH, i, COUNT), ("complete", {COUNT: len(str(item))}))
    return a


def _run(store, clock, actor, wf):
    ex = Executor(store, "spark", {"*": actor}, clock=clock)
    started = ex.start(rule(), wf)
    ex.run_until_idle()
    return ex.run(started["id"])


def _outcomes(store, clock, items, loop=None, fail_at=()):
    doc = _run(store, clock, _actor(items, fail_at), _wf(loop or _loop()))
    return step_state(doc, EACH)["outputs"][OUTCOMES]


# --------------------------------------------------------------------------- one entry per item


def test_outcomes_hold_one_entry_per_item(store, clock):
    assert len(_outcomes(store, clock, ["a", "bb", "ccc"])) == 3


def test_outcomes_are_in_item_order(store, clock):
    got = _outcomes(store, clock, ["a", "bb", "ccc"])
    assert [o["index"] for o in got] == [0, 1, 2]


def test_an_outcome_carries_the_item_as_given(store, clock):
    got = _outcomes(store, clock, [{"id": 11}, {"id": 12}])
    assert [o["item"] for o in got] == [{"id": 11}, {"id": 12}]


def test_an_outcome_without_collect_holds_only_index_item_status_errors(store, clock):
    (only,) = _outcomes(store, clock, ["a"])
    assert sorted(only) == [ERRORS, "index", "item", STATUS]


def test_an_iteration_whose_body_succeeded_is_succeeded(store, clock):
    (only,) = _outcomes(store, clock, ["a"])
    assert only[STATUS] == SUCCEEDED


def test_a_succeeded_iteration_has_no_errors(store, clock):
    (only,) = _outcomes(store, clock, ["a"])
    assert only[ERRORS] == {}


def test_no_items_give_no_outcomes(store, clock):
    assert _outcomes(store, clock, []) == []


# --------------------------------------------------------------------------- skips


def test_a_tolerated_failure_makes_the_iteration_skipped(store, clock):
    got = _outcomes(store, clock, ["a", "bb"], fail_at=(1,))
    assert [o[STATUS] for o in got] == [SUCCEEDED, SKIPPED]


def test_a_tolerated_failure_lists_its_error_message(store, clock):
    got = _outcomes(store, clock, ["a", "bb"], fail_at=(1,))
    assert got[1][ERRORS][FETCH] == "fetch_failed: 404"


def test_the_input_missing_skip_behind_it_lists_its_error_too(store, clock):
    got = _outcomes(store, clock, ["a", "bb"], fail_at=(1,))
    assert got[1][ERRORS][COUNT] == "input 'path' (string) is missing"


def test_errors_name_only_the_skipped_steps_of_that_iteration(store, clock):
    got = _outcomes(store, clock, ["a", "bb"], fail_at=(1,))
    assert sorted(got[1][ERRORS]) == [COUNT, FETCH]


def test_a_false_when_makes_the_iteration_skipped(store, clock):
    loop = _loop(body=_body(dict(SKIP), count_config={"when": NEVER}))
    (only,) = _outcomes(store, clock, ["a"], loop=loop)
    assert only[STATUS] == SKIPPED


def test_a_false_when_adds_no_error(store, clock):
    loop = _loop(body=_body(dict(SKIP), count_config={"when": NEVER}))
    (only,) = _outcomes(store, clock, ["a"], loop=loop)
    assert only[ERRORS] == {}


def test_a_disabled_body_step_makes_the_iteration_skipped(store, clock):
    loop = _loop(body=_body(dict(SKIP), count_enabled=False))
    (only,) = _outcomes(store, clock, ["a"], loop=loop)
    assert only[STATUS] == SKIPPED


def test_a_disabled_body_step_adds_no_error(store, clock):
    loop = _loop(body=_body(dict(SKIP), count_enabled=False))
    (only,) = _outcomes(store, clock, ["a"], loop=loop)
    assert only[ERRORS] == {}


def test_a_failed_body_step_still_fails_the_loop(store, clock):
    loop = _loop(body=_body(fetch_config={}))
    doc = _run(store, clock, _actor(["a"], fail_at=(0,)), _wf(loop))
    assert step_state(doc, EACH)[STATUS] == "failed"


def test_a_failed_body_step_still_fails_the_run(store, clock):
    loop = _loop(body=_body(fetch_config={}))
    doc = _run(store, clock, _actor(["a"], fail_at=(0,)), _wf(loop))
    assert doc[STATUS] == "failed"


# --------------------------------------------------------------------------- collect


def _collecting(collect):
    return _loop(config={"collect": collect})


def test_collect_copies_a_body_steps_output_into_each_entry(store, clock):
    got = _outcomes(store, clock, ["a", "bb"], loop=_collecting({"n": "count.count"}))
    assert [o["n"] for o in got] == [1, 2]


def test_collect_is_null_for_a_skipped_step(store, clock):
    loop = _collecting({"n": "count.count"})
    got = _outcomes(store, clock, ["a", "bb"], loop=loop, fail_at=(1,))
    assert got[1]["n"] is None


def test_collect_is_null_for_an_output_the_step_did_not_give(store, clock):
    (only,) = _outcomes(store, clock, ["a"], loop=_collecting({"why": "count.note"}))
    assert only["why"] is None


def test_collect_keeps_the_entry_keys_plus_its_names(store, clock):
    loop = _collecting({"n": "count.count", "p": "fetch.path"})
    (only,) = _outcomes(store, clock, ["a"], loop=loop)
    assert sorted(only) == [ERRORS, "index", "item", "n", "p", STATUS]


def test_collect_records_a_list_output_as_null(store, clock):
    body = (
        step(FETCH, inputs=(port("item"),), outputs=(port("path", ARRAY),), config=dict(SKIP)),
        step(COUNT, inputs=(port("path", ARRAY),), outputs=(port(COUNT, "integer"),)),
    )
    loop = _loop(config={"collect": {"p": "fetch.path"}}, body=body)
    actor = FakeActor()
    actor.on("list", ("complete", {ITEMS: ["a"]}))
    actor.on(step_key(EACH, 0, FETCH), ("complete", {"path": ["a", "b"]}))
    actor.on(step_key(EACH, 0, COUNT), ("complete", {COUNT: 2}))
    (only,) = step_state(_run(store, clock, actor, _wf(loop)), EACH)["outputs"][OUTCOMES]
    assert only["p"] is None


# --------------------------------------------------------------------------- unchanged


def test_a_loop_without_an_outcomes_port_outputs_as_before(store, clock):
    loop = _loop(outputs=(port(COUNT, ARRAY),))
    doc = _run(store, clock, _actor(["a", "bb"]), _wf(loop))
    assert step_state(doc, EACH)["outputs"] == {COUNT: [1, 2]}


def test_a_loop_without_an_outcomes_port_stores_no_outcomes(store, clock):
    loop = _loop(outputs=(port(COUNT, ARRAY),))
    doc = _run(store, clock, _actor(["a", "bb"]), _wf(loop))
    assert OUTCOMES not in step_state(doc, EACH)


def test_results_beside_outcomes_are_unchanged(store, clock):
    loop = _loop(outputs=(port("results", ARRAY), port(OUTCOMES, ARRAY)))
    doc = _run(store, clock, _actor(["a", "bb"]), _wf(loop))
    assert step_state(doc, EACH)["outputs"]["results"] == [{COUNT: 1}, {COUNT: 2}]


def test_an_outcome_holds_no_body_step_outputs(store, clock):
    doc = _run(store, clock, _actor(["a"]), _wf(_loop()))
    (only,) = step_state(doc, EACH)["outputs"][OUTCOMES]
    assert "path" not in only


# --------------------------------------------------------------------------- validation


def _codes(errors: list[ValidationError], path: str) -> set[str]:
    return {e.code for e in errors if e.path == path}


def _errors_for(collect, loop=None):
    return validate(_wf(loop or _collecting(collect)))


def test_a_valid_collect_validates():
    assert _errors_for({"n": "count.count"}) == []


def test_collect_must_be_an_object():
    assert "collect_invalid" in _codes(_errors_for(["count.count"]), CONFIG)


def test_a_collect_path_must_be_a_string():
    assert "collect_invalid" in _codes(_errors_for({"n": 3}), f"{CONFIG}.n")


def test_a_collect_path_without_a_dot_is_refused():
    assert "collect_invalid" in _codes(_errors_for({"n": "count"}), f"{CONFIG}.n")


def test_a_collect_path_with_two_dots_is_refused():
    assert "collect_invalid" in _codes(_errors_for({"n": "count.count.x"}), f"{CONFIG}.n")


def test_a_collect_path_with_an_empty_part_is_refused():
    assert "collect_invalid" in _codes(_errors_for({"n": ".count"}), f"{CONFIG}.n")


def test_a_collect_path_naming_an_unknown_step_is_refused():
    assert "collect_unknown_step" in _codes(_errors_for({"n": "nope.count"}), f"{CONFIG}.n")


def test_a_collect_path_naming_an_undeclared_output_is_refused():
    assert "collect_unknown_output" in _codes(_errors_for({"n": "count.nope"}), f"{CONFIG}.n")


def test_a_collect_path_into_a_step_without_declared_outputs_is_accepted():
    body = (step(FETCH, inputs=(port("item"),)),)
    loop = _loop(config={"collect": {"n": "fetch.anything"}}, body=body)
    assert validate(workflow((step("list", outputs=(port(ITEMS, ARRAY),)), loop))) == []


def test_a_collect_name_that_is_an_entry_key_is_refused():
    assert "collect_invalid" in _codes(_errors_for({STATUS: "count.count"}), f"{CONFIG}.status")


def test_an_empty_collect_name_is_refused():
    assert "collect_invalid" in _codes(_errors_for({"": "count.count"}), CONFIG)


def test_collect_without_an_outcomes_port_is_refused():
    loop = _loop(outputs=(port(COUNT, ARRAY),), config={"collect": {"n": "count.count"}})
    assert "not_allowed" in _codes(validate(_wf(loop)), CONFIG)


def test_collect_on_a_retry_until_loop_is_refused():
    loop = _loop(kind="retry_until", config={"collect": {"n": "count.count"}})
    assert "not_allowed" in _codes(validate(_wf(loop)), CONFIG)


def test_collect_on_a_plain_step_is_refused():
    plain = step("plain", config={"collect": {"n": "count.count"}})
    assert "not_allowed" in _codes(validate(plain), "config.collect")


def test_an_outcomes_port_that_is_not_an_array_is_refused():
    loop = _loop(outputs=(port(OUTCOMES, "object"),))
    assert "outcomes_type" in _codes(validate(_wf(loop)), "steps[1].outputs")


def test_an_outcomes_port_on_a_retry_until_loop_keeps_its_meaning():
    """retry_until loops are unchanged: their outputs are the last iteration's, any name."""
    loop = _loop(kind="retry_until", outputs=(port(OUTCOMES, "object"),))
    assert validate(_wf(loop)) == []


def test_a_run_start_refuses_a_bad_collect(store, clock):
    ex = Executor(store, "spark", {"*": FakeActor()}, clock=clock)
    wf = _wf(_collecting({"n": "nope.count"}))
    with pytest.raises(RunError) as exc:
        ex.start(rule(), wf)
    assert exc.value.code == "invalid_workflow"
