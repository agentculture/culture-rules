"""POST /workflows/{id}/run: start a workflow directly with typed inputs."""

from __future__ import annotations

from culture_rules.auth.policy import required_role
from culture_rules.model.workflow import Port
from tests.engine.run_helpers import step, workflow
from tests.server.conftest import ALICE


def _put_workflow(client, wf_id="wf", **kw):
    wf = workflow(
        (step("a"),),
        id=wf_id,
        inputs=(
            Port(name="n", type="integer", required=True),
            Port(name="tag", type="string", required=False),
        ),
    )
    body = wf.to_dict()
    body.update(kw)
    assert client.post("/workflows", json=body, headers=ALICE).status_code == 201


def test_run_returns_201_with_the_run_id(client):
    _put_workflow(client)
    r = client.post("/workflows/wf/run", json={"inputs": {"n": 3}}, headers=ALICE)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["id"]
    assert body["workflow_id"] == "wf"
    assert client.get(f"/runs/{body['id']}").json()["id"] == body["id"]


def test_run_without_a_body_uses_empty_inputs(client):
    client.post("/workflows", json=workflow((step("a"),), id="plain").to_dict(), headers=ALICE)
    assert client.post("/workflows/plain/run", headers=ALICE).status_code == 201


def test_invalid_type_names_the_port(client):
    _put_workflow(client)
    r = client.post("/workflows/wf/run", json={"inputs": {"n": "nope"}}, headers=ALICE)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_inputs"
    assert [e["path"] for e in err["errors"]] == ["inputs.n"]
    assert err["errors"][0]["code"]


def test_missing_required_and_unknown_inputs_name_the_port(client):
    _put_workflow(client)
    r = client.post("/workflows/wf/run", json={"inputs": {}}, headers=ALICE)
    assert r.status_code == 422
    assert r.json()["error"]["errors"][0]["path"] == "inputs.n"
    r = client.post("/workflows/wf/run", json={"inputs": {"n": 1, "zzz": 1}}, headers=ALICE)
    assert r.status_code == 422
    assert r.json()["error"]["errors"][0]["path"] == "inputs.zzz"


def test_unknown_workflow_is_404(client):
    r = client.post("/workflows/missing/run", json={"inputs": {}}, headers=ALICE)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "workflow_not_found"


def test_disabled_workflow_is_409(client):
    _put_workflow(client)
    client.post("/workflows/wf/disable", headers=ALICE)
    r = client.post("/workflows/wf/run", json={"inputs": {"n": 1}}, headers=ALICE)
    assert r.status_code == 409


def test_route_needs_the_editor_role():
    assert required_role("POST", "/workflows/wf/run") == "editor"
