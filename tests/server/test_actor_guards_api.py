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
    assert "model_taken" in r.text and "holder" in r.text and "disable holder first" in r.text
    assert store.get("actors", "second") is None


def test_create_disabled_is_allowed_and_enables_once_the_holder_is_disabled(store, client):
    seed(store, actor("holder"))
    r = client.post("/actors", json=actor("second", enabled=False), headers=ALICE)
    assert r.status_code == 201, r.text
    r = client.post("/actors/second/enable", headers=ALICE)
    assert r.status_code == 409 and "holder" in r.text
    assert store.get("actors", "second")["enabled"] is False
    assert client.post("/actors/holder/disable", headers=ALICE).status_code == 200
    assert client.post("/actors/second/enable", headers=ALICE).status_code == 200
    assert store.get("actors", "second")["enabled"] is True


def test_update_to_a_held_model_is_refused_and_a_free_one_passes(store, client):
    seed(store, actor("holder"))
    seed(store, actor("other", model="m2"))
    r = client.put("/actors/other", json=actor("other", model="m1"), headers=ALICE)
    assert r.status_code == 409 and "model_taken" in r.text
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
    assert error_codes(r) and set(error_codes(r)) == {"actor_in_use"}
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
    assert r.status_code == 200 and store.get("actors", "bot")["enabled"] is False
