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
    assert len(recs) == 1
    assert recs[0]["identity"] == "alice"
    assert _audit(store, "off") == []
    assert _audit(store, "gone") == []


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


# ------------------------------------------------------------- t11: workflows/migrate-notes

WORKFLOWS = "workflows"


def _wf(id, description="", notes="", step_desc="", **extra):
    return {
        "id": id,
        "name": f"WF {id}",
        "description": description,
        "notes": notes,
        "steps": [{"id": "s1", "kind": "logic", "description": step_desc, "notes": ""}],
        "revision": 3,
        **extra,
    }


def _seed_notes(store):
    store.put(WORKFLOWS, _wf("old", description=" Keep\nme ", step_desc="step"))
    store.put(WORKFLOWS, _wf("set", description="stays", notes="mine"))
    store.put(WORKFLOWS, _wf("clean"))
    store.put(WORKFLOWS, _wf("gone", description="x", deleted_at="2026-01-01T00:00:00Z"))


def _audits(store, wid):
    return [a for a in store.find("audit") if (a.get("target") or {}).get("id") == wid]


def test_notes_dry_run_lists_and_writes_nothing(client, store):
    _seed_notes(store)
    before = [dict(d) for d in store.find(WORKFLOWS)] + [dict(d) for d in store.find("audit")]
    r = client.post("/workflows/migrate-notes", json={}, headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json() == {
        "workflows": [{"id": "old", "name": "WF old", "nodes": ["workflow", "s1"]}],
        "applied": False,
    }
    assert [dict(d) for d in store.find(WORKFLOWS)] + [
        dict(d) for d in store.find("audit")
    ] == before


def test_notes_apply_moves_audits_once_and_bumps_revision(client, store):
    _seed_notes(store)
    r = client.post("/workflows/migrate-notes", json={"apply": True}, headers=ALICE)
    assert r.status_code == 200, r.text
    assert [w["id"] for w in r.json()["workflows"]] == ["old"] and r.json()["applied"] is True
    doc = store.get(WORKFLOWS, "old")
    assert doc["notes"] == " Keep\nme " and doc["description"] == ""
    assert doc["steps"][0]["notes"] == "step" and doc["steps"][0]["description"] == ""
    assert doc["revision"] == 4
    assert len(_audits(store, "old")) == 1
    for other in ("set", "clean", "gone"):
        assert _audits(store, other) == []
        assert store.get(WORKFLOWS, other)["revision"] == 3
    assert store.get(WORKFLOWS, "set")["description"] == "stays"


def test_notes_second_apply_changes_nothing(client, store):
    _seed_notes(store)
    client.post("/workflows/migrate-notes", json={"apply": True}, headers=ALICE)
    snap = [dict(d) for d in store.find(WORKFLOWS)] + [dict(d) for d in store.find("audit")]
    r = client.post("/workflows/migrate-notes", json={"apply": True}, headers=ALICE)
    assert r.json() == {"workflows": [], "applied": True}
    assert [dict(d) for d in store.find(WORKFLOWS)] + [dict(d) for d in store.find("audit")] == snap


def test_notes_migration_is_admin():
    assert required_role("POST", "/workflows/migrate-notes") == "admin"


# ---- rules migrate-orphans (d13): the fold's d7Candidates, mirrored ----


def _owf(id: str, **extra) -> dict:
    return {"id": id, "name": id, "steps": [], "edges": [], **extra}


def _seed_orphans(store):
    store.put(RULES, _raw("a", name="Adopter One"))  # absent workflow
    store.put(RULES, _raw("b", workflow=None))
    store.put(RULES, _raw("c", workflow={"id": ""}))
    store.put(RULES, _raw("ok", workflow={"id": "w-live"}))
    store.put(RULES, _raw("dangling", workflow={"id": "nowhere"}))
    store.put(RULES, _raw("tomb", workflow={"id": "w-dead"}))
    store.put(RULES, _raw("gone", deleted_at="2026-01-01T00:00:00Z"))
    store.put("workflows", _owf("w-live"))
    store.put("workflows", _owf("w-dead", deleted_at="2026-01-01T00:00:00Z"))
    store.put("workflows", _owf("adopter-one"))


def test_orphans_dry_run_lists_and_writes_nothing(client, store):
    _seed_orphans(store)
    before = [dict(d) for c in (RULES, "workflows", "audit") for d in store.find(c)]
    r = client.post("/rules/migrate-orphans", json={}, headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is False
    assert {x["rule_id"]: x["workflow_id"] for x in body["rules"]} == {
        "a": "adopter-one-2",
        "b": "rule-b",
        "c": "rule-c",
    }
    assert {x["rule_id"]: (x["workflow_id"], x["reason"]) for x in body["not_adopted"]} == {
        "dangling": ("nowhere", "missing"),
        "tomb": ("w-dead", "deleted"),
    }
    assert [dict(d) for c in (RULES, "workflows", "audit") for d in store.find(c)] == before


def test_orphans_apply_makes_d7_workflows_audited_and_is_idempotent(client, store):
    _seed_orphans(store)
    r = client.post("/rules/migrate-orphans", json={"apply": True}, headers=ALICE)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] is True
    for rid, wid in (("a", "adopter-one-2"), ("b", "rule-b"), ("c", "rule-c")):
        assert store.get(RULES, rid)["workflow"] == {"id": wid}
        wf = store.get("workflows", wid)
        assert wf["name"] == store.get(RULES, rid)["name"]
        assert wf["steps"] == [] and wf["edges"] == []
        assert wf["revision"] == 1
        assert len(_audit(store, rid)) == 1 and len(_audit(store, wid)) == 1
    # never written: dangling, deleted-workflow, deleted rule; nothing deleted
    assert store.get(RULES, "dangling")["workflow"] == {"id": "nowhere"}
    assert store.get(RULES, "tomb")["workflow"] == {"id": "w-dead"}
    assert "workflow" not in store.get(RULES, "gone")
    assert _audit(store, "dangling") == [] and _audit(store, "tomb") == []
    assert len(store.find(RULES)) == 7 and len(store.find("workflows")) == 6
    n = len(store.find("audit"))
    again = client.post("/rules/migrate-orphans", json={"apply": True}, headers=ALICE).json()
    assert again["rules"] == []
    assert len(again["not_adopted"]) == 2
    assert len(store.find("audit")) == n


def test_orphans_is_admin_only():
    assert required_role("POST", "/rules/migrate-orphans") == "admin"
