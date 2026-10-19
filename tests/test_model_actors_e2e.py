"""t11: the model-actor flow end to end, as an operator runs it.

``MemoryStore`` + ``create_app`` (fake ``/v1/models`` transport, fake grant resolver) + the real
CLI over an in-process transport: set the gateway allowlist, enrol two models (dry run, then
apply), see ``actors disable`` on the old actor refused while a workflow names it, repoint the
workflow, then disable it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.cli.test_model_actor_verbs import Tap, jfail, jok, wire_to  # noqa: E402
from tests.server.conftest import ALICE  # noqa: E402
from tests.server.test_model_actors_api import (  # noqa: E402
    SPARK,
    _act,
    default_gateways,
    make_client,
    set_gateways,
    target_actor,
)


def _newspaper(actor: str) -> dict:
    return {
        "id": "daily",
        "name": "daily",
        "steps": [{"id": "summary", "kind": "code", "config": _act(actor)}],
        "schema_version": "1.0",
    }


def test_enrol_two_models_repoint_then_disable_the_old_actor(monkeypatch, capsys):
    store = MemoryStore()
    set_gateways(store, {"spark": SPARK})
    client, resolved = make_client(store, default_gateways())
    tap = wire_to(monkeypatch, Tap(client))

    # an old endpoint actor, enabled, and a workflow whose step names it
    old = target_actor("old", params={"base_url": SPARK, "model": "old-model"})
    assert client.post("/actors", json=old, headers=ALICE).status_code == 201
    assert client.post("/workflows", json=_newspaper("old"), headers=ALICE).status_code == 201

    # enrol two models from the fake /v1/models: the dry run writes nothing
    enrol = ["actors", "enrol-models", "--gateway", "spark", "--match", "cortex-spark*"]
    plan = jok(capsys, *enrol)
    assert {c["id"]: c["action"] for c in plan["result"]["changes"]} == {
        "cortex-spark": "create",
        "cortex-spark2": "create",
    }
    assert not {"cortex-spark", "cortex-spark2"} & {a["id"] for a in store.find("actors")}
    done = jok(capsys, *enrol, "--apply")
    assert sorted(a["id"] for a in done["result"]["applied"]) == ["cortex-spark", "cortex-spark2"]
    assert done["result"]["failed"] == []
    assert all(store.get("actors", i)["enabled"] for i in ("cortex-spark", "cortex-spark2"))

    # the workflow still names the old actor: disabling it is refused
    err = jfail(capsys, 1, "actors", "disable", "old", "--apply")
    assert "actor_in_use" in err["message"] and "workflow daily" in err["message"]
    assert store.get("actors", "old")["enabled"] is True

    # repoint the workflow (dry run, then apply); references now show nothing for old
    repoint = ["workflows", "repoint-actor", "daily", "--from", "old", "--to", "cortex-spark2"]
    assert jok(capsys, *repoint)["result"]["changed"] == ["summary"]
    assert jok(capsys, *repoint, "--apply")["applied"] is True
    assert jok(capsys, "actors", "references", "old")["items"] == []
    assert jok(capsys, "actors", "references", "cortex-spark2")["items"][0]["id"] == "daily"

    # now the same disable succeeds, without --force
    assert jok(capsys, "actors", "disable", "old", "--apply")["applied"] is True
    assert store.get("actors", "old")["enabled"] is False
    assert not any("force=true" in u for _, u, _ in tap.calls)
    assert resolved  # the grant was resolved server-side, only after the allowlist check
