"""Rollout documents (#45 t3): GET /rollouts, GET/PUT /rollouts/{id}, strict and audited."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.auth.resolve import LAN, AuthSettings  # noqa: E402
from culture_rules.auth.tokens import ServiceTokens  # noqa: E402
from culture_rules.engine.audit import AUDIT_COLLECTION, MUTATING_VERBS, AuditLog  # noqa: E402
from culture_rules.server.app import _ALL_COLLECTIONS, create_app  # noqa: E402
from culture_rules.server.rollouts import ROLLOUTS_COLLECTION  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.server.conftest import ALICE  # noqa: E402

TS = "2026-10-11T09:00:00+00:00"


def body(version: str = "0.18.12", **changes) -> dict:
    doc = {
        "version": version,
        "snapshot": ["pr-fixer", "nightly"],
        "steps": {
            "pause": {"applied_at": TS, "outcome": "paused"},
            "upgrade:spark": {"applied_at": TS, "outcome": {"from": "0.18.11", "ok": True}},
        },
    }
    doc.update(changes)
    return doc


def test_rollouts_is_a_pre_created_collection():
    assert ROLLOUTS_COLLECTION in _ALL_COLLECTIONS
    assert "rollouts.put" in MUTATING_VERBS


def test_put_then_get_and_list(store, client):
    assert client.get("/rollouts").json() == {"items": []}
    r = client.put("/rollouts/0.18.12", json=body(), headers=ALICE)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["id"] == saved["version"] == "0.18.12"
    assert saved["snapshot"] == ["pr-fixer", "nightly"]
    assert saved["steps"]["upgrade:spark"]["outcome"] == {"from": "0.18.11", "ok": True}
    assert saved["updated_by"] == "alice"
    got = client.get("/rollouts/0.18.12").json()
    assert got == saved
    assert [d["id"] for d in client.get("/rollouts").json()["items"]] == ["0.18.12"]


def test_put_replaces_and_is_audited_once_per_write(store, client):
    client.put("/rollouts/0.18.12", json=body(steps={}), headers=ALICE)
    later = body(steps={"verify": {"applied_at": "2026-10-11T10:00:00Z", "outcome": "green"}})
    client.put("/rollouts/0.18.12", json=later, headers=ALICE)
    assert set(client.get("/rollouts/0.18.12").json()["steps"]) == {"verify"}
    entries = AuditLog.entries(store, target_id="0.18.12", verb="rollouts.put")
    assert len(entries) == 2
    assert entries[0]["identity"] == "alice"
    assert entries[0]["target"] == {"collection": ROLLOUTS_COLLECTION, "id": "0.18.12"}
    assert "steps" in entries[1]["diff"]


def test_get_missing_is_404(client):
    r = client.get("/rollouts/0.0.1")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "rollout_id, payload",
    [
        ("0.18.12", body(version="0.18.13")),  # version must equal the id
        ("0.18.12", {**body(), "extra": 1}),  # unknown top-level field
        ("0.18.12", {k: v for k, v in body().items() if k != "snapshot"}),  # missing
        ("0.18.12", body(snapshot="pr-fixer")),
        ("0.18.12", body(snapshot=["a", "a"])),
        ("0.18.12", body(snapshot=["has space"])),
        ("0.18.12", body(snapshot=[""])),
        ("0.18.12", body(steps={"Bad Name": {"applied_at": TS, "outcome": "x"}})),
        ("0.18.12", body(steps={"pause": {"applied_at": "yesterday", "outcome": "x"}})),
        ("0.18.12", body(steps={"pause": {"applied_at": "2026-10-11T09:00:00", "outcome": "x"}})),
        ("0.18.12", body(steps={"pause": {"applied_at": TS}})),
        ("0.18.12", body(steps={"pause": {"applied_at": TS, "outcome": "x", "by": "me"}})),
        ("0.18.12", body(steps={"pause": {"applied_at": TS, "outcome": 3}})),
        ("0.18.12", body(steps={"pause": {"applied_at": TS, "outcome": "x" * 1001}})),
        ("0.18.12", body(steps={"pause": {"applied_at": TS, "outcome": {"a": {"b": 1}}}})),
        ("0.18.12", body(steps={"pause": {"applied_at": TS, "outcome": {"a b": 1}}})),
        ("0.18.12", body(steps={"p": {"applied_at": TS, "outcome": {"l": list(range(65))}}})),
        ("-bad", body(version="-bad")),
        ("a" * 65, body(version="a" * 65)),
    ],
)
def test_put_is_strict(store, client, rollout_id, payload):
    r = client.put(f"/rollouts/{rollout_id}", json=payload, headers=ALICE)
    assert r.status_code == 422, (payload, r.text)
    assert store.find(ROLLOUTS_COLLECTION) == []
    assert store.find(AUDIT_COLLECTION) == []


def test_outcome_is_bounded_in_bytes(client):
    big = {f"k{i}": "x" * 200 for i in range(30)}
    payload = body(steps={"verify": {"applied_at": TS, "outcome": big}})
    assert client.put("/rollouts/0.18.12", json=payload, headers=ALICE).status_code == 422


def test_secret_literals_are_never_stored(store, client):
    payload = body(steps={"seed": {"applied_at": TS, "outcome": {"github_token": "ghp_x"}}})
    r = client.put("/rollouts/0.18.12", json=payload, headers=ALICE)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "secret_literal"
    assert store.find(ROLLOUTS_COLLECTION) == []
    ref = body(steps={"seed": {"applied_at": TS, "outcome": {"github_token": "grant:GH"}}})
    assert client.put("/rollouts/0.18.12", json=ref, headers=ALICE).status_code == 200


def test_version_strings_with_dev_and_local_parts_are_ids(client):
    for version in ("0.19.0.dev3", "1.0.0rc1", "0.18.12+local"):
        r = client.put(f"/rollouts/{version}", json=body(version=version), headers=ALICE)
        assert r.status_code == 200, r.text


def test_roles_viewer_reads_admin_writes():
    store = MemoryStore()
    client = TestClient(create_app(store, auth=AuthSettings(listener=LAN)))
    tokens = ServiceTokens(store)
    hdr = {
        role: {"Authorization": f"Bearer {tokens.issue('root', name=role, roles=[role]).token}"}
        for role in ("viewer", "editor", "admin")
    }
    assert client.get("/rollouts", headers=hdr["viewer"]).status_code == 200
    for role in ("viewer", "editor"):
        r = client.put("/rollouts/0.18.12", json=body(), headers=hdr[role])
        assert r.status_code == 403, role
    r = client.put("/rollouts/0.18.12", json=body(), headers=hdr["admin"])
    assert r.status_code == 200, r.text
    assert r.json()["updated_by"] == "admin"
    assert client.get("/rollouts/0.18.12", headers=hdr["viewer"]).status_code == 200
