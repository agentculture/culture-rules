"""#45 boundary (c14, h13): a whole rollout, dry runs and every step applied through a fake
runner, never lets a secret value out.

The admin token reaches the CLI only through the environment. It must not appear in any line
the verbs print, in any stored rollout or machine document, or in any argv the runner is given
(ssh and scp never carry it). Install sections name grant secrets; only those names travel.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from culture_rules.cli import _rollout_upgrade as up  # noqa: E402
from culture_rules.cli._commands import rollout  # noqa: E402
from tests.cli.test_nouns_api import run, store, wire  # noqa: E402,F401
from tests.cli.test_rollout_upgrade import (  # noqa: E402
    SPARK2,
    VERSION,
    WHEEL_NAME,
    FakeRunner,
    put_machine,
)
from tests.server.conftest import rule_body, workflow_body  # noqa: E402

RELEASE = {"version": VERSION, "new_rules": ["r-new"], "new_variables": ["fixer_x"]}
SEEDS = [{"name": "fixer_x", "value": 3, "description": "new in this release"}]
GRANT_NAMES = ("RULES_QWEN_FIXER_TOKEN", "RULES_GITHUB_APP_PRIVATE_KEY")


@pytest.fixture(autouse=True)
def release(monkeypatch):
    monkeypatch.setattr(rollout, "_release", lambda: dict(RELEASE))


@pytest.fixture
def fake(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(up, "_RUNNER", runner)
    monkeypatch.setattr(up, "_SLEEP", lambda s: None)
    monkeypatch.setattr(up, "_HOSTNAME", lambda: "spark.tailnet")
    return runner


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    for sub in ("workflows", "rules", "actors"):
        (root / sub).mkdir(parents=True)
    (root / "workflows" / "wf.json").write_text(json.dumps(workflow_body("wf")))
    (root / "rules" / "r-new.json").write_text(json.dumps(rule_body("r-new", enabled=False)))
    (root / "variables.json").write_text(json.dumps(SEEDS))
    (root / "release.json").write_text(json.dumps(RELEASE))
    return root


@pytest.fixture
def wheel(tmp_path):
    path = tmp_path / WHEEL_NAME
    path.write_bytes(b"wheel")
    return path


def _steps(bundle, wheel):
    """Every verb, each first as a dry run and then applied (viewer verbs once)."""
    b, w = ("--bundle", str(bundle)), ("--wheel", str(wheel))
    yield ("rollout", "plan", *w)
    yield ("rollout", "idle")
    for verb in (
        ("pause",),
        ("upgrade", "--host", "spark", *w),
        ("upgrade", "--host", "spark2", *w),
        ("seed", *b),
        ("import", *b),
        ("enable",),
        ("resume",),
    ):
        yield ("rollout", *verb)
        yield ("rollout", *verb, "--apply")
    yield ("rollout", "verify")


def test_a_whole_rollout_never_prints_stores_or_sends_the_token(
    wire, store, capsys, fake, bundle, wheel  # noqa: F811
):
    put_machine(store, "spark", {"mode": "shared_venv"})
    put_machine(store, "spark2", SPARK2)
    printed = []
    for argv in _steps(bundle, wheel):
        for extra in ((), ("--json",)):
            _rc, out, err = run(capsys, *argv, *extra)
            printed.append(out + err)
    stored = json.dumps(
        [store.find("rollouts"), store.find("machines")], sort_keys=True, default=str
    )
    sent = json.dumps(fake.calls)
    for where, text in (("output", "\n".join(printed)), ("store", stored), ("argv", sent)):
        assert wire.token not in text, f"the token leaked into {where}"
    assert fake.calls, "the applied upgrades ran through the fake runner"
    recorded = set(store.get("rollouts", VERSION)["steps"])
    assert {"pause", "seed", "import", "upgrade:spark2"} <= recorded  # the steps really ran


def test_install_secrets_travel_as_grant_names_only(wire, store, capsys, fake, wheel):  # noqa: F811
    put_machine(store, "spark2", SPARK2)
    run(capsys, "rollout", "upgrade", "--host", "spark2", "--wheel", str(wheel), "--apply")
    install = [c for c in fake.calls if any("install.sh" in part for part in c)][-1]
    remote = install[-1]
    for name in GRANT_NAMES:
        assert f"--secret {name}" in remote
    machine = store.get("machines", "spark2")
    assert sorted(machine["install"]["secrets"]) == sorted(GRANT_NAMES)
