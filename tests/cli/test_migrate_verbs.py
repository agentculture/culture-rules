"""t18/d5: ``rules migrate-typeless`` and ``runs backfill-ids`` (dry-run by default)."""

from __future__ import annotations

from tests.cli.test_nouns_api import jrun, run, snapshot, store, wire  # noqa: F401


def _seed(store):  # noqa: F811
    trig = {"kind": "event", "params": {}}
    store.put("rules", {"id": "bad", "name": "Bad", "trigger": trig, "enabled": True})
    store.put("runs", {"id": "a", "rule": {"id": "r1"}})


def test_migrate_typeless_dry_run_changes_nothing(wire, store, capsys):  # noqa: F811
    _seed(store)
    before = snapshot(store)
    out = jrun(capsys, "rules", "migrate-typeless")
    assert out["dry_run"] is True
    assert out["applied"] is False
    assert [r["id"] for r in out["result"]["rules"]] == ["bad"]
    assert snapshot(store) == before


def test_migrate_typeless_apply_disables(wire, store, capsys):  # noqa: F811
    _seed(store)
    out = jrun(capsys, "rules", "migrate-typeless", "--apply")
    assert out["applied"] is True
    assert out["dry_run"] is False
    assert store.get("rules", "bad")["enabled"] is False
    assert len([a for a in store.find("audit") if (a.get("target") or {}).get("id") == "bad"]) == 1


def test_migrate_typeless_text_output(wire, store, capsys):  # noqa: F811
    _seed(store)
    rc, out, _ = run(capsys, "rules", "migrate-typeless")
    assert rc == 0
    assert "bad" in out


def test_backfill_ids_dry_run_then_apply(wire, store, capsys):  # noqa: F811
    _seed(store)
    out = jrun(capsys, "runs", "backfill-ids")
    assert out["dry_run"] is True
    assert out["result"] == {"count": 1, "applied": False}
    assert "rule_id" not in store.get("runs", "a")
    out = jrun(capsys, "runs", "backfill-ids", "--apply")
    assert out["applied"] is True
    assert out["result"]["count"] == 1
    assert store.get("runs", "a")["rule_id"] == "r1"


def _seed_notes(store):  # noqa: F811
    store.put(
        "workflows",
        {
            "id": "w",
            "name": "W",
            "description": "why",
            "notes": "",
            "steps": [{"id": "s", "kind": "logic", "description": "", "notes": ""}],
        },
    )


def test_workflows_migrate_notes_dry_run_then_apply(wire, store, capsys):  # noqa: F811
    _seed_notes(store)
    before = snapshot(store)
    out = jrun(capsys, "workflows", "migrate-notes")
    assert out["dry_run"] is True
    assert out["applied"] is False
    assert out["result"]["workflows"] == [{"id": "w", "name": "W", "nodes": ["workflow"]}]
    assert snapshot(store) == before
    out = jrun(capsys, "workflows", "migrate-notes", "--apply")
    assert out["applied"] is True
    assert store.get("workflows", "w")["notes"] == "why"
    assert store.get("workflows", "w")["description"] == ""
    out = jrun(capsys, "workflows", "migrate-notes", "--apply")
    assert out["result"]["workflows"] == []


def test_migrate_orphans_dry_run_then_apply(wire, store, capsys):  # noqa: F811
    trig = {"kind": "event", "params": {"type": "x.y"}}
    store.put("rules", {"id": "lone", "name": "Lone", "trigger": trig, "enabled": True})
    store.put(
        "rules",
        {"id": "dang", "name": "Dang", "trigger": trig, "enabled": True, "workflow": {"id": "zz"}},
    )
    before = snapshot(store)
    out = jrun(capsys, "rules", "migrate-orphans")
    assert out["dry_run"] is True
    assert [r["workflow_id"] for r in out["result"]["rules"]] == ["lone"]
    assert [r["reason"] for r in out["result"]["not_adopted"]] == ["missing"]
    assert snapshot(store) == before
    out = jrun(capsys, "rules", "migrate-orphans", "--apply")
    assert out["applied"] is True
    assert store.get("rules", "lone")["workflow"] == {"id": "lone"}
    assert jrun(capsys, "rules", "migrate-orphans")["result"]["rules"] == []
