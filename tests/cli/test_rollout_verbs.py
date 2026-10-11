"""``culture-rules rollout``: one step per verb against a real ``create_app(MemoryStore())``."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules import __version__  # noqa: E402
from culture_rules.cli._commands import rollout  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from culture_rules.machines.heartbeat import HEARTBEAT_COLLECTION  # noqa: E402
from tests.cli.test_nouns_api import jrun, run, snapshot, store, wire  # noqa: E402,F401
from tests.server.conftest import rule_body, workflow_body  # noqa: E402

RELEASE = {"version": "9.1.0", "new_rules": ["r-new"], "new_variables": ["fixer_x"]}
SEEDS = [
    {"name": "fixer_x", "value": 3, "description": "new in 9.1.0"},
    {"name": "fixer_old", "value": "keep", "description": "seeded earlier"},
]


@pytest.fixture(autouse=True)
def release(monkeypatch):
    monkeypatch.setattr(rollout, "_release", lambda: dict(RELEASE))
    return RELEASE


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    (root / "workflows").mkdir(parents=True)
    (root / "rules").mkdir()
    (root / "actors").mkdir()
    (root / "workflows" / "wf.json").write_text(json.dumps(workflow_body("wf")))
    (root / "rules" / "r-new.json").write_text(json.dumps(rule_body("r-new", enabled=False)))
    (root / "actors" / "a.json").write_text("{}")  # actors are not part of the import step
    (root / "variables.json").write_text(json.dumps(SEEDS))
    (root / "release.json").write_text(json.dumps(RELEASE))
    return root


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat()


def put_rule(store, rid: str, enabled: bool) -> None:  # noqa: F811
    store.put("rules", {**rule_body(rid), "enabled": enabled})


def put_run(db, rid: str, status: str, *, at: datetime | None = None, error=None) -> None:
    moment = _iso(at or datetime.now(UTC))
    db.put(
        "runs",
        {
            "id": rid,
            "status": status,
            "rule": {"id": "r1"},
            "workflow": {"id": "wf"},
            "steps": [],
            "created_at": moment,
            "finished_at": None if status == "running" else moment,
            "error": error,
        },
    )


def put_node(db, name: str, version: str | None, *, online: bool = True, install=None):
    doc = {"id": name, "name": name, "platform": "linux", "roles": ["engine_node"]}
    if install is not None:
        doc["install"] = install
    db.put("machines", doc)
    ts = datetime.now(UTC) - (timedelta(seconds=1) if online else timedelta(hours=1))
    beat = {"id": name, "machine": name, "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "load": {}}
    if version is not None:
        beat["engine_version"] = version
    db.put(HEARTBEAT_COLLECTION, beat)


def as_role(wire, db, monkeypatch, role: str) -> None:  # noqa: F811
    """Point the CLI at the same app with a ``role`` service token instead of the admin's."""
    from culture_rules.auth.tokens import ServiceTokens
    from culture_rules.cli import _api
    from culture_rules.client.http import ApiClient

    token = ServiceTokens(db).issue("root", name=role, roles=[role]).token
    monkeypatch.setattr(
        _api,
        "make_client",
        lambda api_url=None: ApiClient("http://127.0.0.1:8765", token=token, transport=wire),
    )


def doc(store):  # noqa: F811
    return store.get("rollouts", RELEASE["version"])


def fail(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 1, (out, err)
    return json.loads(err)


# --------------------------------------------------------------------------- registry


def test_verbs_roles_and_dry_run_flags():
    roles = {v.name: (v.role, v.mutating) for v in REGISTRY.verbs("rollout")}
    for name in ("plan", "idle", "verify", "overview", "list", "show"):
        assert roles[name] == ("viewer", False), name
    for name in ("import", "enable"):
        assert roles[name] == ("editor", True), name
    for name in ("pause", "resume", "seed"):
        assert roles[name] == ("admin", True), name
    assert "all" not in roles
    assert "run" not in roles  # no verb chains steps


def test_version_compare():
    assert rollout.is_older("0.18.11", "0.18.12")
    assert rollout.is_older("0.9.0", "0.18.0")
    assert not rollout.is_older("0.18.12", "0.18.12")
    assert not rollout.is_older("0.19.0", "0.18.12")
    assert not rollout.is_older("0.18.0", "0.18")
    assert rollout.is_older("0.18.12.dev3", "0.18.12")
    assert not rollout.is_older("0.18.12", "0.18.12rc1")
    assert rollout.is_older(None, "0.18.12")
    assert rollout.is_older("unknown", "0.18.12")


@pytest.mark.parametrize(
    "older, newer",
    [
        ("1.0rc1", "1.0rc2"),
        ("1.0rc2", "1.0"),
        ("1.0", "1.0.post1"),
        ("1.0rc1", "1.0.post1"),
        ("1.0.dev3", "1.0a1"),
        ("1.0a1", "1.0a2"),
        ("1.0a2", "1.0b1"),
        ("1.0b1", "1.0rc1"),
        ("1.0.dev3", "1.0.dev4"),
        ("1.0a1.dev1", "1.0a1"),
        ("1.0.post1.dev1", "1.0.post1"),
        ("1.0", "1.0+local"),
        ("1.0+abc", "1.0+abc.1"),
        ("1.0+abc", "1.0+5"),
        ("1.0+5", "1.0+10"),
        ("1.0+local", "1.0.post1"),
        ("0.9.9", "0.10.0"),
    ],
)
def test_version_order_follows_pep_440(older, newer):
    assert rollout.version_key(older) < rollout.version_key(newer)
    assert rollout.is_older(older, newer)
    assert not rollout.is_older(newer, older)


@pytest.mark.parametrize(
    "bad", ["2.bad", "1.0-final", "", "v1.0", "1..0", "1.0rc", "1.0 ", None, 3]
)
def test_a_version_that_does_not_parse_is_invalid_and_older(bad):
    assert rollout.version_key(bad) is None
    assert rollout.is_older(bad, "1.0")
    assert rollout.is_older(bad, "0.0.1")


def test_an_invalid_release_makes_every_node_older():
    assert rollout.is_older("1.0", "2.bad")


def test_wheel_version_must_be_this_release(tmp_path):
    wheel = tmp_path / "culture_rules-9.1.0-py3-none-any.whl"
    wheel.write_bytes(b"")
    assert rollout.check_wheel(str(wheel)) == wheel
    older = str(tmp_path / "culture_rules-9.0.0-py3-none-any.whl")
    with pytest.raises(rollout.CliError, match="release 9.0.0"):
        rollout.check_wheel(older)
    with pytest.raises(rollout.CliError, match="not a culture_rules wheel"):
        rollout.check_wheel("other-9.1.0-py3-none-any.whl")
    missing = str(tmp_path / "x" / wheel.name)
    with pytest.raises(rollout.CliError, match="no such wheel"):
        rollout.check_wheel(missing)


def test_install_flags_follow_the_record():
    flags = rollout.install_flags(
        "spark2",
        {
            "mode": "install_sh",
            "secrets": ["RULES_A", "RULES_B"],
            "gate_run_as": "sudo -n -u culture-fixer --",
            "extras": ["store", "events"],
            "wheelhouse": None,
            "python": "3.12",
            "events_host": "broker",
            "events_port": 1883,
        },
    )
    assert flags == [
        "--node-name", "spark2",
        "--secret", "RULES_A", "--secret", "RULES_B",
        "--gate-run-as", "sudo -n -u culture-fixer --",
        "--extras", "store,events",
        "--python", "3.12",
        "--events-host", "broker",
        "--events-port", "1883",
    ]  # fmt: skip
    assert rollout.install_flags("thor", {"mode": "install_sh"}) == ["--node-name", "thor"]


# --------------------------------------------------------------------------- plan / idle


def test_plan_writes_nothing_and_orders_hosts(wire, store, capsys, tmp_path):  # noqa: F811
    put_node(store, "thor", "9.0.0", install={"mode": "install_sh", "secrets": ["RULES_T"]})
    put_node(store, "spark", "9.0.0", install={"mode": "shared_venv"})
    put_node(store, "orin", "9.0.0", install={"mode": "install_sh"})
    put_node(store, "lab", None)
    put_rule(store, "r1", True)
    put_rule(store, "r2", False)
    wheel = tmp_path / "culture_rules-9.1.0-py3-none-any.whl"
    wheel.write_bytes(b"")
    before = snapshot(store)
    out = jrun(capsys, "rollout", "plan", "--wheel", str(wheel))
    assert [h["host"] for h in out["hosts"]] == ["spark", "orin", "thor", "lab"]
    assert out["hosts"][2]["flags"] == ["--node-name", "thor", "--secret", "RULES_T"]
    assert out["hosts"][3]["note"].startswith("not upgradeable")
    assert out["snapshot"] == ["r1"]
    assert out["new_rules"] == ["r-new"]
    assert out["new_variables"] == ["fixer_x"]
    assert wire.mutating() == []
    assert snapshot(store) == before
    rc, text, _ = run(capsys, "rollout", "plan", "--wheel", str(wheel))
    assert rc == 0
    assert "install.sh --node-name thor --secret RULES_T" in text
    assert fail(capsys, "rollout", "plan", "--wheel", "culture_rules-1.0.0-py3-none-any.whl")


def test_idle_lists_non_terminal_runs(wire, store, capsys):  # noqa: F811
    put_run(store, "done", "succeeded")
    assert jrun(capsys, "rollout", "idle")["idle"] is True
    put_run(store, "live", "running")
    out = jrun(capsys, "rollout", "idle")
    assert out["idle"] is False
    assert [r["id"] for r in out["runs"]] == ["live"]


# --------------------------------------------------------------------------- pause / resume


def test_pause_snapshots_before_pausing_and_is_idempotent(wire, store, capsys):  # noqa: F811
    put_rule(store, "r1", True)
    put_rule(store, "r2", False)
    dry = jrun(capsys, "rollout", "pause")
    assert dry["dry_run"] is True
    assert dry["snapshot"] == ["r1"]
    assert doc(store) is None
    assert wire.mutating() == []

    out = jrun(capsys, "rollout", "pause", "--apply")
    assert out["engine"] == "paused"
    calls = wire.mutating()
    snap_first = calls.index(("POST", "/rollouts/9.1.0/snapshot"))
    assert snap_first < calls.index(("POST", "/controls/pause"))  # snapshot lands first
    assert ("PUT", "/rollouts/9.1.0") not in calls  # no read-merge-write
    assert doc(store)["snapshot"] == ["r1"]
    assert doc(store)["steps"]["pause"]["outcome"]["engine"] == "paused"

    put_rule(store, "r1", False)  # e.g. an import disabled it: a re-run keeps the snapshot
    again = jrun(capsys, "rollout", "pause", "--apply")
    assert again["engine"] == "already paused"
    assert again["snapshot_kept"] is True
    assert doc(store)["snapshot"] == ["r1"]


def test_a_second_pause_never_overwrites_the_recorded_snapshot(
    wire, store, capsys, monkeypatch  # noqa: F811
):
    put_rule(store, "r1", True)
    jrun(capsys, "rollout", "pause", "--apply")
    put_rule(store, "r2", True)
    # a stale reader: a concurrent pause read the document before the first one recorded
    real, reads = rollout._rollout_doc, []

    def stale_once(ctx, version):
        reads.append(version)
        return None if len(reads) == 1 else real(ctx, version)

    monkeypatch.setattr(rollout, "_rollout_doc", stale_once)
    out = jrun(capsys, "rollout", "pause", "--apply")
    assert out["snapshot"] == ["r1"]
    assert out["snapshot_kept"] is True
    assert doc(store)["snapshot"] == ["r1"]


def test_steps_recorded_by_interleaved_commands_are_all_kept(wire, store, capsys):  # noqa: F811
    from culture_rules.cli.registry import Context
    from culture_rules.client.http import ApiClient

    ctx_a = Context(ApiClient("http://127.0.0.1:8765", token=wire.token, transport=wire), True)
    ctx_b = Context(ApiClient("http://127.0.0.1:8765", token=wire.token, transport=wire), True)
    wire.calls.clear()
    rollout.record(ctx_a, "upgrade:thor", {"ok": True})
    rollout.record(ctx_b, "upgrade:orin", {"ok": True})
    rollout.record(ctx_a, "seed", {"set": []})
    assert set(doc(store)["steps"]) == {"upgrade:thor", "upgrade:orin", "seed"}
    assert [m for m, _, _ in wire.calls] == ["POST", "POST", "POST"]  # one atomic write each


def test_pause_refuses_non_terminal_runs_unless_forced(wire, store, capsys):  # noqa: F811
    put_run(store, "live", "running")
    err = fail(capsys, "rollout", "pause", "--apply")
    assert "live" in err["message"]
    assert "rollout idle" in err["remediation"]
    assert wire.mutating() == []
    assert doc(store) is None
    out = jrun(capsys, "rollout", "pause", "--force", "--apply")
    assert out["forced"] == ["live"]
    assert out["engine"] == "paused"


def test_resume_reports_already_resumed_and_keeps_the_resume_time(
    wire, store, capsys  # noqa: F811
):
    jrun(capsys, "rollout", "pause", "--apply")
    assert jrun(capsys, "rollout", "resume")["dry_run"] is True
    assert jrun(capsys, "runs", "controls")["paused"] is True
    out = jrun(capsys, "rollout", "resume", "--apply")
    assert out["engine"] == "resumed"
    assert out["controls"]["paused"] is False
    first = doc(store)["steps"]["resume"]["applied_at"]
    again = jrun(capsys, "rollout", "resume", "--apply")
    assert again["engine"] == "already resumed"
    assert doc(store)["steps"]["resume"]["applied_at"] == first


def test_resume_records_its_boundary_before_resuming(wire, store, capsys):  # noqa: F811
    jrun(capsys, "rollout", "pause", "--apply")
    wire.calls.clear()
    jrun(capsys, "rollout", "resume", "--apply")
    assert wire.mutating() == [
        ("POST", "/rollouts/9.1.0/steps/resume_requested"),
        ("POST", "/controls/resume"),
        ("POST", "/rollouts/9.1.0/steps/resume"),
    ]
    steps = doc(store)["steps"]
    assert steps["resume_requested"]["applied_at"] <= steps["resume"]["applied_at"]


def test_resume_is_refused_with_no_engine_change_when_the_boundary_cannot_be_recorded(
    wire, store, capsys, monkeypatch  # noqa: F811
):
    jrun(capsys, "rollout", "pause", "--apply")
    as_role(wire, store, monkeypatch, "editor")
    rc, _, _ = run(capsys, "rollout", "resume", "--apply")
    assert rc == 1
    assert ("POST", "/controls/resume") not in wire.mutating()
    assert jrun(capsys, "runs", "controls")["paused"] is True


def test_a_second_resume_caller_keeps_the_first_boundary(
    wire, store, capsys, monkeypatch  # noqa: F811
):
    """Two callers both read no resume_requested; A records it and resumes; a run fails; B's
    record is refused (409 step_recorded) and B proceeds keeping A's boundary."""
    jrun(capsys, "rollout", "pause", "--apply")
    real = rollout._rollout_doc
    stale = real(_ctx(wire), RELEASE["version"])  # read before A records
    jrun(capsys, "rollout", "resume", "--apply")
    first = doc(store)["steps"]["resume_requested"]
    monkeypatch.setattr(rollout, "_rollout_doc", lambda ctx, version: stale)
    wire.calls.clear()
    out = jrun(capsys, "rollout", "resume", "--apply")
    assert out["engine"] == "already resumed"
    assert ("POST", "/rollouts/9.1.0/steps/resume_requested") in wire.mutating()
    assert doc(store)["steps"]["resume_requested"] == first


def _ctx(wire):  # noqa: F811
    from culture_rules.cli.registry import Context
    from culture_rules.client.http import ApiClient

    return Context(ApiClient("http://127.0.0.1:8765", token=wire.token, transport=wire), True)


# --------------------------------------------------------------------------- seed / import


def test_seed_skips_existing_unless_forced(wire, store, capsys, bundle):  # noqa: F811
    wire.tc.put("/variables/fixer_old", json={"value": "edited", "description": "d"})
    dry = jrun(capsys, "rollout", "seed", "--bundle", str(bundle))
    assert dry["set"] == ["fixer_x"]
    assert dry["skipped"] == ["fixer_old"]
    assert doc(store) is None
    jrun(capsys, "rollout", "seed", "--bundle", str(bundle), "--apply")
    names = {v["name"]: v["value"] for v in jrun(capsys, "variables", "list")["items"]}
    assert names == {"fixer_x": 3, "fixer_old": "edited"}
    assert doc(store)["steps"]["seed"]["outcome"] == {"set": ["fixer_x"], "skipped": ["fixer_old"]}
    forced = jrun(capsys, "rollout", "seed", "--bundle", str(bundle), "--force")
    assert forced["set"] == ["fixer_x", "fixer_old"]


def test_seed_refuses_a_bundle_of_another_release(wire, capsys, bundle):  # noqa: F811
    (bundle / "release.json").write_text(json.dumps({**RELEASE, "version": "8.0.0"}))
    assert "release 8.0.0" in fail(capsys, "rollout", "seed", "--bundle", str(bundle))["message"]


def test_seed_reads_the_wheel_bundle_by_default(wire, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rollout, "_release", lambda: {**RELEASE, "version": __version__})
    out = jrun(capsys, "rollout", "seed")
    assert out["dry_run"] is True
    assert len(out["set"]) == len(rollout.deploy_data.variables())


def test_import_refuses_before_seed_then_imports_workflows_and_rules(
    wire, store, capsys, bundle  # noqa: F811
):
    err = fail(capsys, "rollout", "import", "--bundle", str(bundle), "--apply")
    assert "seed" in err["message"]
    assert "rollout seed --apply" in err["remediation"]
    assert wire.mutating() == []
    jrun(capsys, "rollout", "seed", "--bundle", str(bundle), "--apply")
    dry = jrun(capsys, "rollout", "import", "--bundle", str(bundle))
    assert dry["applied"] is False
    assert dry["would"]["files"] == ["rules/r-new.json", "workflows/wf.json"]
    assert store.get("rules", "r-new") is None
    out = jrun(capsys, "rollout", "import", "--bundle", str(bundle), "--apply")
    assert out["applied"] is True
    assert out["record"] == {"recorded": True}
    assert store.get("rules", "r-new") is not None
    assert store.get("workflows", "wf") is not None
    assert doc(store)["steps"]["import"]["outcome"] == {"add": 2}


# --------------------------------------------------------------------------- enable


def _paused_with(store, capsys, enabled: list[str]):  # noqa: F811
    for rid in enabled:
        put_rule(store, rid, True)
    jrun(capsys, "rollout", "pause", "--apply")
    for rid in enabled:
        put_rule(store, rid, False)  # the import disabled them


def test_enable_refuses_without_a_snapshot(wire, capsys):  # noqa: F811
    err = fail(capsys, "rollout", "enable", "--apply")
    assert "rollout pause --apply" in err["remediation"]


def test_enable_refuses_while_an_online_node_is_older(wire, store, capsys):  # noqa: F811
    _paused_with(store, capsys, ["r1"])
    put_rule(store, "r-new", False)
    put_node(store, "spark", "9.1.0")
    put_node(store, "thor", "9.0.0")
    put_node(store, "lab", None)
    put_node(store, "gone", "8.0.0", online=False)  # offline nodes are not checked
    err = fail(capsys, "rollout", "enable", "--apply")
    assert "thor (9.0.0)" in err["message"]
    assert "lab (unknown)" in err["message"]
    assert "gone" not in err["message"]
    assert "rollout upgrade" in err["remediation"]


def test_enable_refuses_while_a_new_rule_is_not_imported(wire, store, capsys):  # noqa: F811
    _paused_with(store, capsys, ["r1"])
    err = fail(capsys, "rollout", "enable")
    assert "r-new" in err["message"]
    assert "rollout import --apply" in err["remediation"]


def test_enable_refuses_an_enabled_rule_outside_snapshot_plus_new_rules(
    wire, store, capsys  # noqa: F811
):
    _paused_with(store, capsys, ["r1"])
    put_rule(store, "r-new", False)
    put_rule(store, "r-extra", True)
    wire.calls.clear()
    for argv in (("rollout", "enable"), ("rollout", "enable", "--apply")):
        err = fail(capsys, *argv)
        assert "r-extra" in err["message"]
        assert "culture-rules rules disable r-extra --apply" in err["remediation"]
    assert wire.mutating() == []
    assert store.get("rules", "r1")["enabled"] is False


def test_enable_refuses_before_any_write_while_a_snapshot_rule_is_absent(
    wire, store, capsys  # noqa: F811
):
    _paused_with(store, capsys, ["r1", "r-gone"])
    put_rule(store, "r-new", False)
    store.delete("rules", "r-gone")  # deleted since the pause
    wire.calls.clear()
    for argv in (("rollout", "enable"), ("rollout", "enable", "--apply")):
        err = fail(capsys, *argv)
        assert "r-gone" in err["message"]
        assert "rules create" in err["remediation"]
        assert "rollout import --apply" in err["remediation"]
    assert wire.mutating() == []
    assert store.get("rules", "r1")["enabled"] is False
    assert "enable" not in doc(store)["steps"]


def test_enable_output_has_no_missing_field(wire, store, capsys):  # noqa: F811
    _paused_with(store, capsys, ["r1"])
    put_rule(store, "r-new", False)
    assert "missing" not in jrun(capsys, "rollout", "enable")


def test_enable_restores_exactly_snapshot_plus_new_rules(wire, store, capsys):  # noqa: F811
    _paused_with(store, capsys, ["r1", "r2"])
    put_rule(store, "r-new", False)
    put_rule(store, "r-off", False)
    put_rule(store, "r2", True)  # already back on
    wire.calls.clear()
    dry = jrun(capsys, "rollout", "enable")
    assert dry["enable"] == ["r1", "r-new"]
    assert dry["left_disabled"] == ["r-off"]
    assert wire.mutating() == []
    out = jrun(capsys, "rollout", "enable", "--apply")
    assert out["already_enabled"] == ["r2"]
    enabled = sorted(r["id"] for r in store.find("rules") if r.get("enabled"))
    assert enabled == ["r-new", "r1", "r2"]
    assert doc(store)["steps"]["enable"]["outcome"]["left_disabled"] == ["r-off"]
    rc, text, _ = run(capsys, "rollout", "enable")
    assert rc == 0
    assert "left disabled (1): r-off" in text


# --------------------------------------------------------------------------- verify


def _green(store, capsys, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rollout, "_release", lambda: {**RELEASE, "version": __version__})
    put_node(store, "spark", __version__)
    _paused_with(store, capsys, ["r1"])
    put_rule(store, "r1", True)
    put_rule(store, "r-new", True)
    jrun(capsys, "rollout", "resume", "--apply")


def test_verify_green(wire, store, capsys, monkeypatch):  # noqa: F811
    _green(store, capsys, monkeypatch)
    put_run(
        store,
        "old",
        "failed",
        at=datetime.now(UTC) - timedelta(days=1),
        error={"code": "actor_failed", "message": "no_builtin: x"},
    )
    out = jrun(capsys, "rollout", "verify")
    assert out["ok"] is True
    assert all((c["ok"] for c in out["checks"]))


def test_verify_lists_each_failed_check(wire, store, capsys, monkeypatch):  # noqa: F811
    _green(store, capsys, monkeypatch)
    put_node(store, "thor", "0.0.1")
    put_rule(store, "r-extra", True)
    put_run(
        store,
        "bad",
        "failed",
        at=datetime.now(UTC) + timedelta(seconds=5),
        error={"step": "a", "code": "actor_failed", "message": "no_builtin: code step"},
    )
    jrun(capsys, "runs", "pause", "--apply")
    err = fail(capsys, "rollout", "verify")
    msg = err["message"]
    assert "4 of 5 check(s) failed" in msg
    for part in ("- nodes:", "thor=0.0.1", "- engine:", "- rules:", "r-extra", "bad"):
        assert part in msg
    assert "- api:" not in msg


def test_verify_fails_before_any_step(wire, store, capsys):  # noqa: F811
    msg = fail(capsys, "rollout", "verify")["message"]
    assert "4 of 5" in msg
    assert "no resume step recorded" in msg  # the engine is running


def put_failed(db, rid: str, *, created: datetime, finished: datetime, error=None) -> None:
    db.put(
        "runs",
        {
            "id": rid,
            "status": "failed",
            "rule": {"id": "r1"},
            "workflow": {"id": "wf"},
            "steps": [],
            "created_at": _iso(created),
            "finished_at": _iso(finished),
            "error": error,
        },
    )


NO_BUILTIN = {"code": "actor_failed", "message": "no_builtin: code step"}


def test_verify_reads_every_failed_run_since_resume(wire, store, capsys, monkeypatch):  # noqa: F811
    _green(store, capsys, monkeypatch)
    now = datetime.now(UTC)
    for i in range(1_001):  # newest created, but finished a day before the resume
        put_failed(
            store,
            f"noise{i:04d}",
            created=now + timedelta(hours=1),
            finished=now - timedelta(days=1),
        )
    # created days before the resume, failed after it: a created_at window misses it
    put_failed(
        store,
        "old-created",
        created=now - timedelta(days=3),
        finished=now + timedelta(seconds=5),
        error=NO_BUILTIN,
    )
    msg = fail(capsys, "rollout", "verify")["message"]
    assert "old-created" in msg


def test_verify_pages_until_the_listing_is_exhausted(
    wire, store, capsys, monkeypatch  # noqa: F811
):  # noqa: F811
    _green(store, capsys, monkeypatch)
    monkeypatch.setattr(rollout, "_RUNS_PAGE", 25)
    now = datetime.now(UTC)
    for i in range(60):
        put_failed(store, f"f{i:03d}", created=now, finished=now + timedelta(seconds=i + 1))
    put_failed(store, "last", created=now, finished=now + timedelta(minutes=5), error=NO_BUILTIN)
    assert "last" in fail(capsys, "rollout", "verify")["message"]


def test_verify_fails_closed_when_the_listing_is_incomplete(
    wire, store, capsys, monkeypatch  # noqa: F811
):
    _green(store, capsys, monkeypatch)
    monkeypatch.setattr(rollout, "_RUNS_PAGE", 10)
    monkeypatch.setattr(rollout, "_MAX_RUN_PAGES", 2)
    now = datetime.now(UTC)
    for i in range(30):
        put_failed(store, f"f{i:03d}", created=now, finished=now + timedelta(seconds=i + 1))
    msg = fail(capsys, "rollout", "verify")["message"]
    assert "could not read every failed run" in msg


def test_verify_fails_closed_on_an_api_without_run_paging(
    wire, store, capsys, monkeypatch  # noqa: F811
):
    _green(store, capsys, monkeypatch)
    real = rollout._get

    def old_api(ctx, path, query=None):
        answer = real(ctx, path, query)
        if path == "/runs":
            answer.pop("next_cursor", None)
        return answer

    monkeypatch.setattr(rollout, "_get", old_api)
    assert "could not read every failed run" in fail(capsys, "rollout", "verify")["message"]


def test_verify_counts_a_failure_between_the_resume_response_and_its_recording(
    wire, store, capsys, monkeypatch  # noqa: F811
):
    from culture_rules.server import rollouts as server_rollouts

    class ServerClock(datetime):
        at = datetime.now(UTC)

        @classmethod
        def now(cls, tz=None):
            return cls.at

    monkeypatch.setattr(server_rollouts, "datetime", ServerClock)
    monkeypatch.setattr(rollout, "_release", lambda: {**RELEASE, "version": __version__})
    put_node(store, "spark", __version__)
    _paused_with(store, capsys, ["r1"])
    put_rule(store, "r1", True)
    put_rule(store, "r-new", True)
    raced_at = ServerClock.at + timedelta(seconds=10)
    real = wire.tc.request

    def racing(method, url, **kw):
        answer = real(method, url, **kw)
        if (method, url) == ("POST", "/controls/resume"):
            # the engine runs again: a run fails before the resume step is recorded
            put_failed(store, "raced", created=raced_at, finished=raced_at, error=NO_BUILTIN)
            ServerClock.at = raced_at + timedelta(seconds=10)
        return answer

    monkeypatch.setattr(wire.tc, "request", racing)
    jrun(capsys, "rollout", "resume", "--apply")
    steps = store.get("rollouts", __version__)["steps"]
    resumed = datetime.fromisoformat(steps["resume"]["applied_at"])
    assert resumed > raced_at  # a window opening at the resume step would miss the run
    assert "raced" in fail(capsys, "rollout", "verify")["message"]


def test_verify_falls_back_to_the_resume_step_of_an_older_document(
    wire, store, capsys, monkeypatch  # noqa: F811
):
    _green(store, capsys, monkeypatch)
    legacy = store.get("rollouts", __version__)
    legacy["steps"].pop("resume_requested")
    store.put("rollouts", legacy)
    assert jrun(capsys, "rollout", "verify")["ok"] is True
    later = datetime.now(UTC) + timedelta(seconds=5)
    put_failed(store, "after", created=later, finished=later, error=NO_BUILTIN)
    assert "after" in fail(capsys, "rollout", "verify")["message"]
