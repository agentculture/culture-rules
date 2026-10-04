"""t18/d5: POST /rules/migrate-typeless and POST /runs/backfill-ids (admin, dry-run default)."""

from __future__ import annotations

from culture_rules.auth.policy import required_role
from tests.server.conftest import ALICE

RULES = "rules"


def _raw(id: str, params=None, **extra) -> dict:
    return {
        "id": id,
        "name": f"Rule {id}",
        "trigger": {"kind": "event", "params": params or {}},
        "enabled": True,
        **extra,
    }


def _audit(store, rule_id):
    return [a for a in store.find("audit") if (a.get("target") or {}).get("id") == rule_id]


def _seed(store):
    store.put(RULES, _raw("bad"))
    store.put(RULES, _raw("good", {"type": "x.y"}))
    store.put(RULES, _raw("off", enabled=False))
    store.put(RULES, _raw("gone", deleted_at="2026-01-01T00:00:00Z"))


def test_dry_run_lists_and_changes_nothing(client, store):
    _seed(store)
    before = [dict(d) for d in store.find(RULES)] + [dict(d) for d in store.find("audit")]
    r = client.post("/rules/migrate-typeless", json={}, headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False
    assert {x["id"]: x["enabled_before"] for x in body["rules"]} == {"bad": True, "off": False}
    assert body["rules"][0]["name"].startswith("Rule ")
    assert [dict(d) for d in store.find(RULES)] + [dict(d) for d in store.find("audit")] == before


def test_apply_disables_with_one_audit_record_each_and_deletes_nothing(client, store):
    _seed(store)
    r = client.post("/rules/migrate-typeless", json={"apply": True}, headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True
    assert store.get(RULES, "bad")["enabled"] is False
    assert store.get(RULES, "good")["enabled"] is True
    assert store.get(RULES, "gone")["enabled"] is True  # skipped
    assert len(store.find(RULES)) == 4
    recs = _audit(store, "bad")
    assert len(recs) == 1 and recs[0]["identity"] == "alice"
    assert _audit(store, "off") == [] and _audit(store, "gone") == []


def test_second_apply_is_idempotent(client, store):
    _seed(store)
    client.post("/rules/migrate-typeless", json={"apply": True}, headers=ALICE)
    n = len(store.find("audit"))
    r = client.post("/rules/migrate-typeless", json={"apply": True}, headers=ALICE)
    assert r.status_code == 200
    assert {x["id"]: x["enabled_before"] for x in r.json()["rules"]} == {
        "bad": False,
        "off": False,
    }
    assert len(store.find("audit")) == n


def test_backfill_ids_dry_run_and_apply(client, store):
    store.put("runs", {"id": "a", "rule": {"id": "r1"}, "workflow": {"id": "w1"}})
    r = client.post("/runs/backfill-ids", json={}, headers=ALICE)
    assert r.json() == {"count": 1, "applied": False}
    assert "rule_id" not in store.get("runs", "a")
    r = client.post("/runs/backfill-ids", json={"apply": True}, headers=ALICE)
    assert r.json() == {"count": 1, "applied": True}
    assert store.get("runs", "a")["rule_id"] == "r1"
    assert (
        client.post("/runs/backfill-ids", json={"apply": True}, headers=ALICE).json()["count"] == 0
    )


def test_both_routes_are_admin_only():
    assert required_role("POST", "/rules/migrate-typeless") == "admin"
    assert required_role("POST", "/runs/backfill-ids") == "admin"
