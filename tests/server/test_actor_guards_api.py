"""model_taken on actor create/update/enable and actor_in_use on actor disable."""

from __future__ import annotations

import pytest

from tests.server.conftest import ALICE

URL = "http://127.0.0.1:8001"


def actor(id: str, model: str | None = "m1", *, enabled: bool = True, base_url=URL, **extra):
    params = {}
    if base_url:
        params["base_url"] = base_url
    if model:
        params["model"] = model
    return {"id": id, "name": id, "kind": "agent", "enabled": enabled, "params": params, **extra}


def seed(store, doc):
    store.put("actors", {**doc, "schema_version": "1.0"})


def error_codes(r):
    return [e.get("code") for e in r.json()["error"]["errors"]]


# --------------------------------------------------------------------------- model_taken


def test_create_names_the_holder_and_says_to_disable_it_first(store, client):
    seed(store, actor("holder"))
    r = client.post("/actors", json=actor("second"), headers=ALICE)
    assert r.status_code == 409, r.text
    assert "model_taken" in r.text
    assert "holder" in r.text
    assert "disable holder first" in r.text
    assert store.get("actors", "second") is None


def test_create_disabled_is_allowed_and_enables_once_the_holder_is_disabled(store, client):
    seed(store, actor("holder"))
    r = client.post("/actors", json=actor("second", enabled=False), headers=ALICE)
    assert r.status_code == 201, r.text
    r = client.post("/actors/second/enable", headers=ALICE)
    assert r.status_code == 409
    assert "holder" in r.text
    assert store.get("actors", "second")["enabled"] is False
    assert client.post("/actors/holder/disable", headers=ALICE).status_code == 200
    assert client.post("/actors/second/enable", headers=ALICE).status_code == 200
    assert store.get("actors", "second")["enabled"] is True


def test_update_to_a_held_model_is_refused_and_a_free_one_passes(store, client):
    seed(store, actor("holder"))
    seed(store, actor("other", model="m2"))
    r = client.put("/actors/other", json=actor("other", model="m1"), headers=ALICE)
    assert r.status_code == 409
    assert "model_taken" in r.text
    assert store.get("actors", "other")["params"]["model"] == "m2"
    assert (
        client.put("/actors/other", json=actor("other", model="m3"), headers=ALICE).status_code
        == 200
    )
    # an actor updating itself is never its own holder
    assert (
        client.put(
            "/actors/holder", json=actor("holder", description="x"), headers=ALICE
        ).status_code
        == 200
    )


def test_update_without_a_body_id_is_an_id_mismatch_not_its_own_holder(store, client):
    seed(store, actor("holder"))
    body = actor("holder", description="x")
    del body["id"]
    r = client.put("/actors/holder", json=body, headers=ALICE)
    assert "model_taken" not in error_codes(r), r.text


def test_actors_outside_the_rule_never_conflict(store, client):
    seed(store, actor("holder"))
    # no base_url (mesh-reached): shares the model name freely
    r = client.post("/actors", json=actor("mesh", base_url=None), headers=ALICE)
    assert r.status_code == 201, r.text
    seed(store, actor("off", model="m9", enabled=False))
    seed(store, actor("deleted", model="m8", deleted_at="x"))
    assert client.post("/actors", json=actor("n1", model="m9"), headers=ALICE).status_code == 201
    assert client.post("/actors", json=actor("n2", model="m8"), headers=ALICE).status_code == 201


# --------------------------------------------------------------------------- actor_in_use


def put_workflow(store, id="wf", *, enabled=True, **step_extra):
    steps = [
        {
            "id": "a",
            "kind": "action",
            "config": {"action": {"kind": "message", "params": {"actor": "bot"}}},
        },
        {
            "id": "loop",
            "kind": "for_each",
            "body": [
                {
                    "id": "inner",
                    "kind": "action",
                    "config": {"action": {"kind": "message", "params": {"actor": "bot"}}},
                }
            ],
        },
    ]
    store.put("workflows", {"id": id, "enabled": enabled, "steps": steps, "revision": 1})


def test_disable_lists_enabled_workflow_and_rule_references(store, client):
    seed(store, actor("bot", model=None))
    put_workflow(store)
    store.put("rules", {"id": "r-act", "action": {"kind": "message", "params": {"actor": "bot"}}})
    store.put("rules", {"id": "r-place", "placement": {"actor": "bot"}})
    r = client.post("/actors/bot/disable", headers=ALICE)
    assert r.status_code == 409, r.text
    assert error_codes(r)
    assert set(error_codes(r)) == {"actor_in_use"}
    found = {(e["path"], e["message"]) for e in r.json()["error"]["errors"]}
    assert found == {
        ("workflows/wf", "workflow wf uses bot (action at a)"),
        ("workflows/wf", "workflow wf uses bot (action at loop.body[inner])"),
        ("rules/r-act", "rule r-act uses bot (action at action)"),
        ("rules/r-place", "rule r-place uses bot (placement at placement)"),
    }
    assert store.get("actors", "bot")["enabled"] is True


def test_disabled_and_deleted_referrers_do_not_count(store, client):
    seed(store, actor("bot", model=None))
    put_workflow(store, enabled=False)
    store.put("rules", {"id": "r1", "enabled": False, "placement": {"actor": "bot"}})
    store.put("rules", {"id": "r2", "placement": {"actor": "bot"}, "deleted_at": "x"})
    r = client.post("/actors/bot/disable", headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False


@pytest.mark.parametrize("force", ["true", "1"])
def test_force_disables_anyway(store, client, force):
    seed(store, actor("bot", model=None))
    store.put("rules", {"id": "r1", "placement": {"actor": "bot"}})
    assert client.post("/actors/bot/disable", headers=ALICE).status_code == 409
    r = client.post(f"/actors/bot/disable?force={force}", headers=ALICE)
    assert r.status_code == 200
    assert store.get("actors", "bot")["enabled"] is False


# --------------------------------------------------------------------------- restore, import


def test_restore_of_a_second_holder_is_refused_until_the_holder_is_disabled(store, client):
    seed(store, actor("old"))
    assert client.delete("/actors/old", headers=ALICE).status_code == 200
    seed(store, actor("new"))
    r = client.post("/actors/old/restore", headers=ALICE)
    assert r.status_code == 409, r.text
    assert error_codes(r) == ["model_taken"]
    assert store.get("actors", "old")["deleted_at"]
    assert client.post("/actors/new/disable", headers=ALICE).status_code == 200
    assert client.post("/actors/old/restore", headers=ALICE).status_code == 200


def _files(*docs):
    import json

    return {f"actors/{d['id']}.json": json.dumps({**d, "schema_version": "1.0"}) for d in docs}


@pytest.mark.parametrize("apply", [False, True])
def test_import_of_a_second_holder_is_refused(store, client, apply):
    seed(store, actor("holder"))
    r = client.post(
        "/import", json={"files": _files(actor("second")), "apply": apply}, headers=ALICE
    )
    assert r.status_code == 409, r.text
    assert error_codes(r) == ["model_taken"]
    assert r.json()["error"]["errors"][0]["path"] == "actors/second"
    assert store.get("actors", "second") is None


def test_import_that_disables_the_holder_and_adds_the_next_passes(store, client):
    seed(store, actor("holder"))
    files = _files(actor("holder", enabled=False), actor("second"))
    r = client.post("/import", json={"files": files, "apply": True}, headers=ALICE)
    assert r.status_code == 200, r.text
    assert store.get("actors", "holder")["enabled"] is False
    assert store.get("actors", "second")["enabled"] is True


def test_import_of_two_holders_of_one_model_is_refused(store, client):
    r = client.post("/import", json={"files": _files(actor("a"), actor("b"))}, headers=ALICE)
    assert r.status_code == 409, r.text
    assert set(error_codes(r)) == {"model_taken"}


# --------------------------------------------------------------------------- delete


def test_delete_of_an_actor_in_use_is_refused(store, client):
    seed(store, actor("bot", model=None, enabled=False))
    put_workflow(store)
    r = client.delete("/actors/bot", headers=ALICE)
    assert r.status_code == 409, r.text
    assert set(error_codes(r)) == {"actor_in_use"}
    assert "force=true to delete anyway" in r.json()["error"]["message"]
    assert not store.get("actors", "bot").get("deleted_at")


def test_delete_with_force_or_without_enabled_referrers_passes(store, client):
    seed(store, actor("bot", model=None))
    seed(store, actor("idle", model=None))
    put_workflow(store)
    assert client.delete("/actors/idle", headers=ALICE).status_code == 200
    assert client.delete("/actors/bot?force=true", headers=ALICE).status_code == 200
    assert store.get("actors", "bot")["deleted_at"]
