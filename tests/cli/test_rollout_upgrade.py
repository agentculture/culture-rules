"""``culture-rules rollout upgrade``: ssh/scp or the shared venv, through a fake runner only."""

from __future__ import annotations

import json
import shlex

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules import deploy_data  # noqa: E402
from culture_rules.cli import _rollout_upgrade as up  # noqa: E402
from culture_rules.cli._commands import rollout  # noqa: E402
from culture_rules.cli.verbs import REGISTRY  # noqa: E402
from tests.cli.test_nouns_api import jrun, run, snapshot, store, wire  # noqa: E402,F401

VERSION = "9.1.0"
WHEEL_NAME = f"culture_rules-{VERSION}-py3-none-any.whl"
GATE = "sudo -n -u culture-fixer -- /usr/bin/env PATH=/home/culture-fixer/.local/bin:/usr/bin"
SPARK2 = {
    "mode": "install_sh",
    "secrets": ["RULES_QWEN_FIXER_TOKEN", "RULES_GITHUB_APP_PRIVATE_KEY"],
    "gate_run_as": GATE,
    "extras": ["store", "events"],
    "wheelhouse": "wh",
    "python": "3.12",
    "events_host": "broker",
    "events_port": 1883,
}
NODE_STATE = (
    "Id=culture-rules-node.service\nActiveState=active\nActiveEnterTimestamp=Sat 2026-10-11"
)


class FakeRunner:
    """Records every argv; answers by the command's shape."""

    def __init__(self, *, version: str = VERSION, fail: str | None = None):
        self.calls: list[list[str]] = []
        self.version = version
        self.fail = fail

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        text = " ".join(argv)
        if self.fail and self.fail in text:
            return up.Result(1, "", "boom")
        if "--version" in text:
            return up.Result(0, f"culture-rules {self.version}\n")
        if "show" in argv or "show" in text:
            return up.Result(0, NODE_STATE + "\n")
        return up.Result(0, "")


@pytest.fixture(autouse=True)
def release(monkeypatch):
    monkeypatch.setattr(rollout, "_release", lambda: {"version": VERSION, "new_rules": []})


@pytest.fixture
def fake(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(up, "_RUNNER", runner)
    monkeypatch.setattr(up, "_SLEEP", lambda s: None)
    monkeypatch.setattr(up, "_HOSTNAME", lambda: "spark.tailnet")
    return runner


@pytest.fixture
def wheel(tmp_path):
    path = tmp_path / WHEEL_NAME
    path.write_bytes(b"wheel")
    return path


def put_machine(db, name: str, install=None, address=None) -> None:
    doc = {"id": name, "name": name, "platform": "linux", "roles": ["engine_node"]}
    if install is not None:
        doc["install"] = install
    if address:
        doc["address"] = address
    db.put("machines", doc)


def outcome(db, name):
    return db.get("rollouts", VERSION)["steps"][f"upgrade:{name}"]["outcome"]


def test_upgrade_is_admin_and_mutating():
    verb = REGISTRY.get("rollout", "upgrade")
    assert verb.role == "admin"
    assert verb.mutating
    assert {p.name for p in verb.params} == {"host", "wheel"}


def test_set_runner_swaps_and_returns_the_previous(monkeypatch):
    monkeypatch.setattr(up, "_RUNNER", up._RUNNER)
    fake = FakeRunner()
    previous = up.set_runner(fake)
    assert up._RUNNER is fake
    assert up.set_runner(previous) is fake


def test_dry_run_prints_the_exact_argv_and_runs_nothing(
    wire, store, capsys, fake, wheel  # noqa: F811
):
    put_machine(store, "spark2", SPARK2, address="spark2.tail")
    before = snapshot(store)
    out = jrun(capsys, "rollout", "upgrade", "--host", "spark2", "--wheel", str(wheel))
    assert fake.calls == []
    assert wire.mutating() == []
    assert snapshot(store) == before
    deploy = f"culture-rules-deploy-{VERSION}"
    mkdir, scp, install, version, state = out["would"]
    assert mkdir == ["ssh", "-o", "BatchMode=yes", "spark2.tail", f"mkdir -p {deploy}"]
    assert scp == [
        "scp", "-o", "BatchMode=yes", "-q",
        str(deploy_data.install_script()), str(wheel), f"spark2.tail:{deploy}/",
    ]  # fmt: skip
    assert install[:4] == ["ssh", "-o", "BatchMode=yes", "spark2.tail"]
    assert shlex.split(install[4]) == [
        "bash", f"{deploy}/install.sh",
        "--node-name", "spark2",
        "--wheel", f"{deploy}/{WHEEL_NAME}",
        "--secret", "RULES_QWEN_FIXER_TOKEN", "--secret", "RULES_GITHUB_APP_PRIVATE_KEY",
        "--gate-run-as", GATE,
        "--extras", "store,events",
        "--wheelhouse", "wh",
        "--python", "3.12",
        "--events-host", "broker",
        "--events-port", "1883",
        "--apply",
    ]  # fmt: skip
    assert version[-1].endswith("venv/bin/culture-rules --version")
    assert "ActiveEnterTimestamp" in state[-1]
    assert "culture-rules-node.service" in state[-1]
    rc, text, _ = run(capsys, "rollout", "upgrade", "--host", "spark2", "--wheel", str(wheel))
    assert rc == 0
    assert "dry-run: would run" in text
    assert "scp -o BatchMode=yes" in text


def test_apply_runs_install_sh_and_records_the_outcome(
    wire, store, capsys, fake, wheel  # noqa: F811
):
    put_machine(store, "thor", {"mode": "install_sh"})
    out = jrun(capsys, "rollout", "upgrade", "--host", "thor", "--wheel", str(wheel), "--apply")
    assert [c[0] for c in fake.calls] == ["ssh", "scp", "ssh", "ssh", "ssh"]
    assert "install.sh --node-name thor --wheel" in fake.calls[2][-1]
    assert out["outcome"]["installed_version"] == VERSION
    recorded = outcome(store, "thor")
    assert recorded["ok"] is True
    assert recorded["ActiveState"] == "active"
    assert recorded["ActiveEnterTimestamp"] == "Sat 2026-10-11"
    assert recorded["mode"] == "install_sh"
    assert recorded["wheel"] == WHEEL_NAME


def test_a_failed_command_stops_records_and_exits_nonzero(
    wire, store, capsys, fake, wheel  # noqa: F811
):
    fake.fail = "--node-name"  # the install.sh run
    put_machine(store, "thor", {"mode": "install_sh"})
    rc, _, err = run(
        capsys, "rollout", "upgrade", "--host", "thor", "--wheel", str(wheel), "--apply", "--json"
    )
    assert rc == 2
    assert "run install.sh failed" in json.loads(err)["message"]
    assert len(fake.calls) == 3  # nothing after the failure
    assert outcome(store, "thor")["ok"] is False


def test_a_wrong_installed_version_is_a_failure(wire, store, capsys, fake, wheel):  # noqa: F811
    fake.version = "9.0.0"
    put_machine(store, "thor", {"mode": "install_sh"})
    rc, _, err = run(
        capsys, "rollout", "upgrade", "--host", "thor", "--wheel", str(wheel), "--apply", "--json"
    )
    assert rc == 2
    assert "9.0.0" in json.loads(err)["message"]


def test_shared_venv_never_runs_install_sh(wire, store, capsys, fake, wheel):  # noqa: F811
    put_machine(store, "spark", {"mode": "shared_venv"})
    dry = jrun(capsys, "rollout", "upgrade", "--host", "spark", "--wheel", str(wheel))
    install, restart, _version, _state = dry["would"]
    assert install[:3] == ["uv", "pip", "install"]
    assert install[-4:] == ["--reinstall-package", "culture-rules", "--no-deps", str(wheel)]
    assert restart == [
        "systemctl", "--user", "restart", "culture-rules-api.service", "culture-rules-node.service"
    ]  # fmt: skip
    jrun(capsys, "rollout", "upgrade", "--host", "spark", "--wheel", str(wheel), "--apply")
    assert not any("install.sh" in " ".join(c) for c in fake.calls)
    assert not any(c[0] in ("ssh", "scp") for c in fake.calls)
    assert outcome(store, "spark")["ok"] is True


def test_shared_venv_runs_only_on_its_own_host(
    wire, store, capsys, fake, wheel, monkeypatch  # noqa: F811
):
    monkeypatch.setattr(up, "_HOSTNAME", lambda: "thor")
    put_machine(store, "spark", {"mode": "shared_venv"})
    rc, _, err = run(
        capsys, "rollout", "upgrade", "--host", "spark", "--wheel", str(wheel), "--apply", "--json"
    )
    assert rc == 1
    assert "this host is thor" in json.loads(err)["message"]
    assert fake.calls == []


def test_a_record_without_install_exits_1_naming_the_section(
    wire, store, capsys, fake, wheel  # noqa: F811
):
    put_machine(store, "orin")
    rc, _, err = run(
        capsys, "rollout", "upgrade", "--host", "orin", "--wheel", str(wheel), "--apply", "--json"
    )
    assert rc == 1
    assert "no install section" in json.loads(err)["message"]
    assert fake.calls == []


def test_unknown_machine_or_wrong_wheel_fails_before_any_command(
    wire, store, capsys, fake, wheel, tmp_path  # noqa: F811
):
    rc, _, _ = run(capsys, "rollout", "upgrade", "--host", "nope", "--wheel", str(wheel), "--apply")
    assert rc == 1
    put_machine(store, "thor", {"mode": "install_sh"})
    other = tmp_path / "culture_rules-9.0.0-py3-none-any.whl"
    other.write_bytes(b"")
    rc, _, _ = run(capsys, "rollout", "upgrade", "--host", "thor", "--wheel", str(other), "--apply")
    assert rc == 1
    assert fake.calls == []


def test_the_runner_never_sees_the_api_url_or_token(monkeypatch):
    monkeypatch.setenv("CULTURE_RULES_TOKEN", "secret-token-value")
    monkeypatch.setenv("CULTURE_RULES_API_URL", "http://127.0.0.1:9")
    env = up._minimal_env()
    assert "CULTURE_RULES_TOKEN" not in env
    assert "CULTURE_RULES_API_URL" not in env
    seen = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        seen["argv"] = argv

        class Done:
            returncode, stdout, stderr = 0, "ok", ""

        return Done()

    monkeypatch.setattr(up.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(up.subprocess, "run", fake_run)
    assert up._subprocess_runner(["ssh", "-o", "BatchMode=yes", "h", "true"], 5) == up.Result(
        0, "ok", ""
    )
    assert seen["argv"][0] == "/usr/bin/ssh"
    assert "shell" not in seen
    assert "CULTURE_RULES_TOKEN" not in seen["env"]
    assert seen["timeout"] == 5


def test_the_runner_reports_a_missing_executable(monkeypatch):
    monkeypatch.setattr(up.shutil, "which", lambda name: None)
    with pytest.raises(up.CliError, match="not on PATH"):
        up._subprocess_runner(["ssh"], 5)
