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
    assert out["dry_run"] is True and out["applied"] is False
    assert [r["id"] for r in out["result"]["rules"]] == ["bad"]
    assert snapshot(store) == before


def test_migrate_typeless_apply_disables(wire, store, capsys):  # noqa: F811
    _seed(store)
    out = jrun(capsys, "rules", "migrate-typeless", "--apply")
    assert out["applied"] is True and out["dry_run"] is False
    assert store.get("rules", "bad")["enabled"] is False
    assert len([a for a in store.find("audit") if (a.get("target") or {}).get("id") == "bad"]) == 1


def test_migrate_typeless_text_output(wire, store, capsys):  # noqa: F811
    _seed(store)
    rc, out, _ = run(capsys, "rules", "migrate-typeless")
    assert rc == 0 and "bad" in out


def test_backfill_ids_dry_run_then_apply(wire, store, capsys):  # noqa: F811
    _seed(store)
    out = jrun(capsys, "runs", "backfill-ids")
    assert out["dry_run"] is True and out["result"] == {"count": 1, "applied": False}
    assert "rule_id" not in store.get("runs", "a")
    out = jrun(capsys, "runs", "backfill-ids", "--apply")
    assert out["applied"] is True and out["result"]["count"] == 1
    assert store.get("runs", "a")["rule_id"] == "r1"
