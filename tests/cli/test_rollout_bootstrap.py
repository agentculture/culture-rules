"""``culture-rules rollout`` against an API that predates it (no ``/rollouts`` endpoints).

The release that ships the rollout CLI is bootstrapped once by hand; every verb that needs
the rollout document refuses with exit 2 and points at the runbook's bootstrap section.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import httpx  # noqa: E402

from culture_rules import __version__  # noqa: E402
from culture_rules.cli import _rollout_upgrade as up  # noqa: E402
from culture_rules.cli._commands import rollout  # noqa: E402
from tests.cli.test_nouns_api import jrun, run, snapshot, store, wire  # noqa: E402,F401
from tests.cli.test_rollout_upgrade import FakeRunner, put_machine  # noqa: E402
from tests.cli.test_rollout_verbs import RELEASE, put_rule  # noqa: E402

BOOTSTRAP = "Bootstrapping the first release"


@pytest.fixture(autouse=True)
def release(monkeypatch):
    monkeypatch.setattr(rollout, "_release", lambda: dict(RELEASE))


def _old_api(wire, monkeypatch, write_status: int = 404):  # noqa: F811
    """Make the in-process API look like 0.18.8: no /rollouts routes, no /health version."""
    real = wire.tc.request

    def old(method, url, **kw):
        if url.split("?")[0].startswith("/rollouts"):
            status = 404 if method == "GET" else write_status
            detail = "Not Found" if status == 404 else "Method Not Allowed"
            return httpx.Response(status, json={"detail": detail})
        answer = real(method, url, **kw)
        if url == "/health":
            body = answer.json()
            body.pop("version", None)
            return httpx.Response(answer.status_code, json=body)
        return answer

    monkeypatch.setattr(wire.tc, "request", old)


def _env_fail(capsys, *argv):
    rc, out, err = run(capsys, *argv, "--json")
    assert rc == 2, (out, err)
    return json.loads(err)


def _assert_bootstrap(err):
    assert "has no /rollouts endpoints" in err["message"]
    assert "predates the rollout CLI" in err["message"]
    assert "http://127.0.0.1:8765" in err["message"]
    assert "docs/operations/rollout.md" in err["remediation"]
    assert BOOTSTRAP in err["remediation"]


@pytest.mark.parametrize("status", [404, 405])
def test_pause_refuses_before_pausing(wire, store, capsys, monkeypatch, status):  # noqa: F811
    put_rule(store, "r1", True)
    _old_api(wire, monkeypatch, status)
    _assert_bootstrap(_env_fail(capsys, "rollout", "pause", "--apply"))
    assert ("POST", "/controls/pause") not in wire.mutating()
    assert jrun(capsys, "runs", "controls")["paused"] is False


def test_resume_refuses_before_resuming(wire, store, capsys, monkeypatch):  # noqa: F811
    wire.tc.post("/controls/pause")
    _old_api(wire, monkeypatch)
    _assert_bootstrap(_env_fail(capsys, "rollout", "resume", "--apply"))
    assert ("POST", "/controls/resume") not in wire.mutating()
    assert jrun(capsys, "runs", "controls")["paused"] is True


@pytest.mark.parametrize("status", [404, 405])
def test_upgrade_runs_nothing(wire, store, capsys, monkeypatch, tmp_path, status):  # noqa: F811
    runner = FakeRunner(version=RELEASE["version"])
    monkeypatch.setattr(up, "_RUNNER", runner)
    put_machine(store, "thor", {"mode": "install_sh"})
    wheel = tmp_path / f"culture_rules-{RELEASE['version']}-py3-none-any.whl"
    wheel.write_bytes(b"wheel")
    _old_api(wire, monkeypatch, status)
    err = _env_fail(
        capsys, "rollout", "upgrade", "--host", "thor", "--wheel", str(wheel), "--apply"
    )
    _assert_bootstrap(err)
    assert runner.calls == []


def test_a_step_record_names_the_bootstrap(wire, store, capsys, monkeypatch):  # noqa: F811
    from culture_rules.cli._errors import EXIT_ENV_ERROR, CliError
    from culture_rules.cli.registry import Context
    from culture_rules.client.http import ApiClient

    _old_api(wire, monkeypatch, 405)
    ctx = Context(ApiClient("http://127.0.0.1:8765", token=wire.token, transport=wire), True)
    with pytest.raises(CliError) as caught:
        rollout.record(ctx, "seed", {"set": []})
    assert caught.value.code == EXIT_ENV_ERROR
    assert BOOTSTRAP in caught.value.remediation


def test_a_real_404_from_a_new_api_is_not_the_bootstrap(wire, store, capsys):  # noqa: F811
    """The rollout document of a release not yet started is absent (404), not a missing
    endpoint: show still answers a plain not-found user error."""
    rc, _, err = run(capsys, "rollout", "show", "0.0.1", "--json")
    assert rc == 1
    assert BOOTSTRAP not in json.loads(err)["remediation"]


def _wheel(tmp_path):
    wheel = tmp_path / f"culture_rules-{RELEASE['version']}-py3-none-any.whl"
    wheel.write_bytes(b"")
    return str(wheel)


def test_plan_reports_the_bootstrap_on_an_old_api(
    wire, store, capsys, monkeypatch, tmp_path  # noqa: F811
):
    put_rule(store, "r1", True)
    _old_api(wire, monkeypatch)
    before = snapshot(store)
    out = jrun(capsys, "rollout", "plan", "--wheel", _wheel(tmp_path))
    assert out["api_version"] is None
    assert out["rollouts_endpoints"] is False
    assert any(BOOTSTRAP in line for line in out["lines"])
    assert any("has no /rollouts endpoints" in line for line in out["lines"])
    assert wire.mutating() == []
    assert snapshot(store) == before


def test_plan_reports_the_api_version_on_a_current_api(wire, store, capsys, tmp_path):  # noqa: F811
    out = jrun(capsys, "rollout", "plan", "--wheel", _wheel(tmp_path))
    assert out["api_version"] == __version__
    assert out["rollouts_endpoints"] is True
    assert not any(BOOTSTRAP in line for line in out["lines"])
    assert any(f"API version {__version__}" in line for line in out["lines"])
