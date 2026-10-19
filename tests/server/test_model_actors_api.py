"""POST /actors/enrol-models, GET /actors/{id}/references, POST /workflows/{id}/repoint-actor.

The gateway allowlist is the shared variable ``model_gateways`` (machine -> URL); the grant
resolver and the ``/v1/models`` transport are injected, so a refusal can be shown never to
resolve a secret or reach the network.
"""

from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from culture_rules.actors import trusted
from culture_rules.actors.trusted import workflow_digest, workflow_digest_v2
from culture_rules.engine.audit import AUDIT_COLLECTION
from tests.server.conftest import ALICE, dev_app

ROOT = Path(__file__).resolve().parents[2]
KEY = "key-" + uuid.uuid4().hex  # built at runtime: never a committed literal
SPARK = "http://127.0.0.1:8001"
SPARK2 = "http://127.0.0.1:8000/v1"
LIVE_MODELS = [
    "cortex-spark",
    "cortex-spark2",
    "worker-thor",
    "hand-spark2",
    "embedder-qwen",
    "reranker-qwen",
    "unsloth/Qwen3.8-27B-NVFP4",
]


class FakeGateways:
    """``transport(url, headers, timeout) -> (status, body bytes)`` over fixed model lists."""

    def __init__(self, lists: dict[str, list[str]], status: int = 200) -> None:
        self.lists = lists
        self.status = status
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers, timeout):
        self.calls.append((url, dict(headers)))
        models = self.lists.get(url)
        if models is None:
            raise OSError("connection refused")
        body = {"object": "list", "data": [{"id": m, "object": "model"} for m in models]}
        return self.status, json.dumps(body).encode()


def make_client(store, transport=None, resolve=None):
    resolved: list[str] = []

    def fake_resolve(ref):
        resolved.append(ref)
        return KEY

    app = dev_app(store, resolve_secret=resolve or fake_resolve, models_transport=transport)
    return TestClient(app), resolved


def set_gateways(store, value):
    """A dict is written in the list form a variable can hold: ["machine=URL", ...]."""
    if isinstance(value, dict):
        value = [f"{k}={v}" for k, v in value.items()]
    store.put_variable("model_gateways", value, updated_by="alice")


def default_gateways():
    return FakeGateways(
        {
            f"{SPARK}/v1/models": LIVE_MODELS,
            f"{SPARK2}/models": ["cortex-spark2", "cortex-spark", "embedder-qwen"],
        }
    )


def enrol(client, **body):
    payload = {"gateways": ["spark"], "match": ["cortex-*"], **body}
    return client.post("/actors/enrol-models", json=payload, headers=ALICE)


# --------------------------------------------------------------------------- enrol-models


def test_dry_run_plans_only_match_hits_and_writes_nothing(store):
    set_gateways(store, {"spark": SPARK, "spark2": SPARK2})
    t = default_gateways()
    client, resolved = make_client(store, t)
    r = enrol(client, gateways=["spark", "spark2"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    plan = {c["id"]: c["action"] for c in body["changes"]}
    assert plan == {"cortex-spark": "create", "cortex-spark2": "create"}
    assert body["also_served_by"] == {"cortex-spark": ["spark2"], "cortex-spark2": ["spark2"]}
    assert body["applied"] == []
    assert [g["machine"] for g in body["gateways"]] == ["spark", "spark2"]
    assert store.find("actors") == []
    assert resolved == ["grant:LOBES_GATEWAY_API_KEY"]
    # the bearer goes to the allow-listed URLs only, and never comes back out
    assert [u for u, _ in t.calls] == [f"{SPARK}/v1/models", f"{SPARK2}/models"]
    assert all(h["Authorization"] == f"Bearer {KEY}" for _, h in t.calls)
    assert KEY not in r.text


def test_dry_run_is_the_default_and_apply_writes_one_audited_actor_each(store):
    set_gateways(store, {"spark": SPARK})
    client, _ = make_client(store, default_gateways())
    assert enrol(client).json()["dry_run"] is True
    r = enrol(client, dry_run=False, pools=["cortex-spark2=qwen-spark2"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert sorted((a["id"], a["action"]) for a in body["applied"]) == [
        ("cortex-spark", "create"),
        ("cortex-spark2", "create"),
    ]
    assert body["failed"] == []
    stored = store.get("actors", "cortex-spark2")
    assert stored["kind"] == "agent" and stored["machine"] == "spark"
    assert stored["params"]["base_url"] == SPARK
    assert stored["params"]["model"] == "cortex-spark2"
    assert stored["params"]["concurrency_pool"] == "qwen-spark2"
    assert stored["params"]["api_key"] == "grant:LOBES_GATEWAY_API_KEY"
    assert KEY not in json.dumps(store.find("actors"))
    assert KEY not in r.text
    entries = [e for e in store.find(AUDIT_COLLECTION) if e["target"]["collection"] == "actors"]
    assert sorted(e["target"]["id"] for e in entries) == ["cortex-spark", "cortex-spark2"]
    assert {e["identity"] for e in entries} == {"alice"}
    # a second run plans nothing new
    again = enrol(client, pools=["cortex-spark2=qwen-spark2"]).json()
    assert {c["action"] for c in again["changes"]} == {"unchanged"}


def test_update_touches_only_enrolled_fields_and_is_audited(store):
    set_gateways(store, {"spark": SPARK})
    client, _ = make_client(store, default_gateways())
    enrol(client, dry_run=False, match=["cortex-spark"])
    doc = store.get("actors", "cortex-spark")
    store.put("actors", {**doc, "description": "hand edited", "machine": "thor"})
    r = enrol(client, dry_run=False, match=["cortex-spark"])
    assert [(a["id"], a["action"]) for a in r.json()["applied"]] == [("cortex-spark", "update")]
    after = store.get("actors", "cortex-spark")
    assert (after["machine"], after["description"]) == ("spark", "hand edited")
    updates = [e for e in store.find(AUDIT_COLLECTION) if e["verb"] == "definitions.update"]
    assert [(e["target"]["id"], e["identity"]) for e in updates] == [("cortex-spark", "alice")]


def test_unknown_gateway_is_refused_naming_the_known_ones_before_any_secret(store):
    set_gateways(store, {"spark": SPARK, "spark2": SPARK2})
    t = default_gateways()
    client, resolved = make_client(store, t)
    r = enrol(client, gateways=["spark", "evil"])
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "unknown_gateway"
    assert "evil" in err["message"] and "spark, spark2" in err["message"]
    assert resolved == [] and t.calls == []


def test_no_model_gateways_variable_refuses_with_no_known_names(store):
    t = default_gateways()
    client, resolved = make_client(store, t)
    r = enrol(client)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unknown_gateway"
    assert "model_gateways" in r.json()["error"]["message"]
    assert resolved == [] and t.calls == []


def test_a_model_gateways_change_is_picked_up_on_the_next_call(store):
    set_gateways(store, {"spark2": SPARK2})
    t = default_gateways()
    client, _ = make_client(store, t)
    assert enrol(client).status_code == 422
    set_gateways(store, {"spark": SPARK})
    r = enrol(client)
    assert r.status_code == 200
    assert t.calls[-1][0] == f"{SPARK}/v1/models"
    set_gateways(store, {"spark": "http://127.0.0.1:9999"})
    t.lists["http://127.0.0.1:9999/v1/models"] = ["cortex-new"]
    assert [c["id"] for c in enrol(client).json()["changes"]] == ["cortex-new"]


@pytest.mark.parametrize(
    "value",
    [
        ["spark"],
        {"spark": "file:///etc/passwd"},
        ["spark=7"],
        "http://x",
        [f"spark={SPARK}", f"spark={SPARK2}"],
        ["=http://127.0.0.1:1"],
    ],
)
def test_a_malformed_allowlist_is_refused_before_any_secret(store, value):
    set_gateways(store, value)
    t = default_gateways()
    client, resolved = make_client(store, t)
    r = enrol(client)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "invalid_model_gateways"
    assert resolved == [] and t.calls == []


def test_an_object_valued_allowlist_is_read_too(store, monkeypatch):
    real = store.get_variable

    def fake(name):
        if name == "model_gateways":
            return {"name": name, "value": {"spark": SPARK}}
        return real(name)

    monkeypatch.setattr(store, "get_variable", fake)
    client, _ = make_client(store, default_gateways())
    r = enrol(client)
    assert r.status_code == 200, r.text
    assert r.json()["gateways"][0]["url"] == SPARK


@pytest.mark.parametrize("key", ["sk-literal", "", "grant:"])
def test_api_key_must_be_a_grant_reference(store, key):
    set_gateways(store, {"spark": SPARK})
    t = default_gateways()
    client, resolved = make_client(store, t)
    r = enrol(client, api_key=key)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_api_key"
    assert resolved == [] and t.calls == []
    assert "sk-literal" not in r.text  # a literal is never echoed back


def test_empty_match_is_422(store):
    set_gateways(store, {"spark": SPARK})
    t = default_gateways()
    client, resolved = make_client(store, t)
    r = enrol(client, match=[])
    assert r.status_code == 422
    assert resolved == [] and t.calls == []


def test_bad_pool_is_422(store):
    set_gateways(store, {"spark": SPARK})
    client, _ = make_client(store, default_gateways())
    r = enrol(client, pools=["nopool"])
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_pool"


def test_an_unresolvable_grant_is_409_without_its_text(store):
    set_gateways(store, {"spark": SPARK})
    t = default_gateways()

    def boom(ref):
        raise RuntimeError("secret detail that must not leak")

    client, _ = make_client(store, t, resolve=boom)
    r = enrol(client)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "secret_unavailable"
    assert "must not leak" not in r.text
    assert t.calls == []


def test_an_unreachable_or_failing_gateway_is_502_naming_it(store):
    set_gateways(store, {"spark": "http://127.0.0.1:1"})
    client, _ = make_client(store, default_gateways())
    r = enrol(client)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "gateway_unavailable"
    assert "spark" in r.json()["error"]["message"]
    set_gateways(store, {"spark": SPARK})
    failing = default_gateways()
    failing.status = 401
    client, _ = make_client(store, failing)
    r = enrol(client)
    assert r.status_code == 502 and KEY not in r.text


def test_a_model_held_by_another_enabled_endpoint_actor_is_refused_in_the_plan(store):
    set_gateways(store, {"spark": SPARK})
    store.put(
        "actors",
        {
            "id": "newspaper-llm",
            "name": "newspaper-llm",
            "kind": "agent",
            "machine": "spark",
            "enabled": True,
            "params": {"base_url": SPARK, "model": "cortex-spark2"},
            "schema_version": "1.0",
        },
    )
    client, _ = make_client(store, default_gateways())
    r = enrol(client, dry_run=False)
    changes = {c["id"]: c for c in r.json()["changes"]}
    assert changes["cortex-spark"]["action"] == "create"
    assert changes["cortex-spark2"]["action"] == "refused"
    assert changes["cortex-spark2"]["reason"] == "model_taken: newspaper-llm (disable it first)"
    assert [a["id"] for a in r.json()["applied"]] == ["cortex-spark"]
    assert store.get("actors", "cortex-spark2") is None


def test_a_model_id_held_by_an_app_actor_is_refused_and_left_untouched(store):
    set_gateways(store, {"spark": SPARK})
    app_actor = {
        "id": "cortex-spark",
        "name": "cortex-spark",
        "kind": "app",
        "params": {"surface": "github"},
        "schema_version": "1.0",
    }
    store.put("actors", app_actor)
    before = copy.deepcopy(store.get("actors", "cortex-spark"))
    client, _ = make_client(store, default_gateways())
    r = enrol(client, dry_run=False, match=["cortex-spark"])
    assert r.json()["changes"][0]["action"] == "refused"
    assert store.get("actors", "cortex-spark") == before


def test_enrol_models_is_admin_only(store):
    from culture_rules.auth.policy import required_role

    assert required_role("POST", "/actors/enrol-models") == "admin"


# --------------------------------------------------------------------------- references


def _act(actor):
    params = {"actor": actor, "content": "Summarise the day."}
    return {"builtin": "action", "action": {"kind": "llm.complete", "params": params}}


def news_workflow(actor="old", id="news", **extra):
    return {
        "id": id,
        "name": id,
        "description": f"Summaries by {actor} daily",
        "notes": f"{actor} is the model",
        "steps": [
            {
                "id": "summarise",
                "kind": "for_each",
                "max_iterations": 10,
                "config": {"items": "inputs.items"},
                "body": [
                    {
                        "id": "summary",
                        "kind": "code",
                        "config": _act(actor),
                        "notes": f"talks to {actor}",
                    }
                ],
            },
            {"id": "placed", "kind": "code", "placement": {"actor": actor}},
        ],
        "schema_version": "1.0",
        **extra,
    }


def target_actor(id="new", **extra):
    return {
        "id": id,
        "name": id,
        "kind": "agent",
        "machine": "spark",
        "params": {"base_url": SPARK, "model": id},
        "schema_version": "1.0",
        **extra,
    }


def seeded(store, **wf_extra):
    client, _ = make_client(store)
    created = client.post("/workflows", json=news_workflow(**wf_extra), headers=ALICE)
    assert created.status_code == 201, created.text
    for actor in (target_actor("old"), target_actor("new")):
        assert client.post("/actors", json=actor, headers=ALICE).status_code == 201
    return client


def test_references_lists_workflow_steps_and_rules(store):
    client = seeded(store)
    store.put(
        "rules",
        {"id": "r1", "enabled": False, "action": {"kind": "message", "params": {"actor": "old"}}},
    )
    store.put("rules", {"id": "gone", "action": {"params": {"actor": "old"}}, "deleted_at": "x"})
    r = client.get("/actors/old/references", headers=ALICE)
    assert r.status_code == 200, r.text
    got = {(i["kind"], i["id"], i["path"], i["via"], i["enabled"]) for i in r.json()["items"]}
    assert got == {
        ("workflow", "news", "summarise.body[summary]", "action", True),
        ("workflow", "news", "placed", "placement", True),
        ("rule", "r1", "action", "action", False),
    }
    assert r.json()["actor_id"] == "old"
    assert client.get("/actors/new/references", headers=ALICE).json()["items"] == []


def test_references_of_a_missing_actor_is_404(store):
    client, _ = make_client(store)
    r = client.get("/actors/nope/references", headers=ALICE)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


# --------------------------------------------------------------------------- repoint-actor


def repoint(client, id="news", **body):
    payload = {"from": "old", "to": "new", "expected_revision": 1, **body}
    return client.post(f"/workflows/{id}/repoint-actor", json=payload, headers=ALICE)


def test_repoint_dry_run_plans_paths_warnings_and_digest_without_writing(store):
    client = seeded(store)
    before = copy.deepcopy(store.get("workflows", "news"))
    audits = len(store.find(AUDIT_COLLECTION))
    r = repoint(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["workflow_id"] == "news"
    assert body["changed"] == ["summarise.body[summary]"]
    assert any("notes mention 'old'" in w for w in body["warnings"])
    assert any("placed on actor 'old'" in w for w in body["warnings"])
    assert body["revision"] == 1
    assert body["digest"].startswith("sha256:") and body["digest_v2"].startswith("sha256:")
    assert store.get("workflows", "news") == before
    assert len(store.find(AUDIT_COLLECTION)) == audits


def test_repoint_apply_writes_through_the_revision_path_and_audits_once(store):
    client = seeded(store)
    audits = len(store.find(AUDIT_COLLECTION))
    r = repoint(client, dry_run=False)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["revision"] == 2
    stored = store.get("workflows", "news")
    assert stored["revision"] == 2
    step = stored["steps"][0]["body"][0]
    assert step["config"]["action"]["params"]["actor"] == "new"
    assert step["notes"] == "talks to old"
    assert stored["description"] == "Summaries by old daily"
    assert stored["notes"] == "old is the model"
    assert stored["steps"][1]["placement"]["actor"] == "old"
    assert body["digest"] == workflow_digest(stored)
    assert body["digest_v2"] == workflow_digest_v2(stored)
    new = store.find(AUDIT_COLLECTION)[audits:]
    assert [(e["verb"], e["target"]["id"], e["identity"]) for e in new] == [
        ("definitions.update", "news", "alice")
    ]


def test_repoint_stale_revision_is_409_revision_conflict(store):
    client = seeded(store)
    for dry in (True, False):
        r = repoint(client, expected_revision=0, dry_run=dry)
        assert r.status_code == 409
        err = r.json()["error"]
        assert err["code"] == "revision_conflict"
        assert err["errors"][0]["current_revision"] == 1
    r = client.post(
        "/workflows/news/repoint-actor", json={"from": "old", "to": "new"}, headers=ALICE
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "revision_conflict"
    assert store.get("workflows", "news")["revision"] == 1


def test_repoint_refuses_a_missing_disabled_or_deleted_target(store):
    client = seeded(store)
    r = repoint(client, to="ghost")
    assert (r.status_code, r.json()["error"]["code"]) == (422, "target_not_found")
    client.post("/actors/new/disable", headers=ALICE)
    r = repoint(client, dry_run=False)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "target_disabled")
    client.post("/actors/new/enable", headers=ALICE)
    client.delete("/actors/new", headers=ALICE)
    r = repoint(client)
    assert (r.status_code, r.json()["error"]["code"]) == (409, "target_deleted")
    assert store.get("workflows", "news")["revision"] == 1


def test_repoint_with_no_matching_step_is_refused(store):
    client = seeded(store)
    r = repoint(client, **{"from": "nobody"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "no_matching_step"
    r = repoint(client, to="old")
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "same_actor"


def test_repoint_of_a_missing_workflow_is_404(store):
    client = seeded(store)
    assert repoint(client, id="nope").status_code == 404


def _publish_fix():
    path = ROOT / "docs" / "rules" / "pr-fixer" / "workflows" / "publish-fix.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_repoint_of_a_v2_trusted_workflow_is_refused_without_force(store):
    client, _ = make_client(store)
    wf = _publish_fix()
    old_v2 = workflow_digest_v2(wf)
    store.put("workflows", {**wf, "revision": 1})
    client.post("/actors", json=target_actor("new"), headers=ALICE)
    for dry in (True, False):
        r = repoint(client, id=wf["id"], **{"from": "github-app"}, dry_run=dry)
        assert r.status_code == 409, r.text
        err = r.json()["error"]
        assert err["code"] == "workflow_trusted"
        assert old_v2 in err["message"]
    r = repoint(client, id=wf["id"], **{"from": "github-app"}, dry_run=False, force=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trusted_digest"] == old_v2
    assert body["digest_v2"] != old_v2
    assert body["digest_v2"] == workflow_digest_v2(store.get("workflows", wf["id"]))
    assert any("trust" in w for w in body["warnings"])


def test_repoint_of_a_v1_trusted_workflow_is_refused_without_force(store, monkeypatch):
    client = seeded(store)
    v1 = workflow_digest(store.get("workflows", "news"))
    monkeypatch.setitem(trusted.TRUSTED_WORKFLOWS, "pr-fix", frozenset({v1}))
    r = repoint(client)
    assert r.status_code == 409
    assert v1 in r.json()["error"]["message"]
    r = repoint(client, force=True)
    assert r.status_code == 200
    assert r.json()["trusted_digest"] == v1
    assert r.json()["digest"] != v1
