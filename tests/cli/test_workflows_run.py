"""``workflows run``: dry-run by default, --apply starts, inputs as --input / --inputs-json."""

from __future__ import annotations

import pytest

from culture_rules.model.workflow import Port
from tests.cli.test_nouns_api import jrun, run, snapshot, store, wire  # noqa: F401
from tests.engine.run_helpers import step, workflow

H = {"X-Culture-Identity": "alice"}


@pytest.fixture
def wf(wire):  # noqa: F811
    body = workflow(
        (step("a"),),
        id="wf",
        inputs=(
            Port(name="n", type="integer", required=True),
            Port(name="tag", type="string", required=False),
        ),
    ).to_dict()
    assert wire.tc.post("/workflows", json=body, headers=H).status_code == 201
    wire.calls.clear()


def test_dry_run_sends_nothing_mutating(wire, store, wf, capsys):  # noqa: F811
    before = snapshot(store)
    out = jrun(capsys, "workflows", "run", "wf", "--input", "n=3", "--input", "tag=x")
    assert out["dry_run"] is True
    assert out["applied"] is False
    assert out["would"] == {
        "method": "POST",
        "path": "/workflows/wf/run",
        "body": {"inputs": {"n": 3, "tag": "x"}},
    }
    assert wire.mutating() == []
    assert snapshot(store) == before


def test_apply_starts_a_run(wire, wf, capsys):  # noqa: F811
    out = jrun(capsys, "workflows", "run", "wf", "--input", "n=3", "--apply")
    assert out["applied"] is True
    run_id = out["result"]["id"]
    assert wire.tc.get(f"/runs/{run_id}").json()["workflow_id"] == "wf"


def test_inputs_json_and_input_override(wire, wf, capsys):  # noqa: F811
    out = jrun(
        capsys, "workflows", "run", "wf", "--inputs-json", '{"n": 1, "tag": "a"}', "--input", "n=2"
    )
    assert out["would"]["body"] == {"inputs": {"n": 2, "tag": "a"}}


def test_value_not_json_is_a_string(wire, wf, capsys):  # noqa: F811
    out = jrun(capsys, "workflows", "run", "wf", "--input", "tag=hello world")
    assert out["would"]["body"]["inputs"] == {"tag": "hello world"}


def test_bad_input_syntax_is_a_user_error(wire, wf, capsys):  # noqa: F811
    rc, _, err = run(capsys, "workflows", "run", "wf", "--input", "novalue")
    assert rc == 1
    assert "name=value" in err


def test_invalid_inputs_on_apply_exit_1_naming_the_port(wire, wf, capsys):  # noqa: F811
    rc, out, err = run(capsys, "workflows", "run", "wf", "--input", "n=abc", "--apply")
    assert rc == 1
    assert "inputs.n" in (out + err)
