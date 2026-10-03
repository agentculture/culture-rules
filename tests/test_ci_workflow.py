"""Pin the CI contract: the web job and the four enforced gates (t35)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "tests.yml"
SHA_RE = re.compile(r"@[0-9a-f]{40}\b")


@pytest.fixture(scope="module")
def wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: dict) -> list[dict]:
    return job["steps"]


def _runs(job: dict) -> str:
    return "\n".join(s.get("run", "") for s in _steps(job))


def test_existing_jobs_kept(wf):
    for name in ("test", "lint", "harness-smoke", "version-check", "web"):
        assert name in wf["jobs"], name


def test_web_job_steps(wf):
    web = wf["jobs"]["web"]
    uses = [s.get("uses", "") for s in _steps(web)]
    setup = [u for u in uses if "actions/setup-node" in u]
    assert setup, "setup-node must be SHA-pinned"
    assert all(SHA_RE.search(u) for u in setup), "setup-node must be SHA-pinned"
    runs = _runs(web)
    for needle in (
        "npm ci",
        "npm run typecheck",
        "npm test",
        "npm run check",
        "npm run build",
        "playwright install",
        "npm run test:e2e",
        "webglass",
    ):
        assert needle in runs, needle
    assert "#agent-state" in runs
    assert "ready" in runs


def test_web_job_order(wf):
    runs = [s.get("run", "") for s in _steps(wf["jobs"]["web"])]
    idx = {k: next(i for i, r in enumerate(runs) if k in r) for k in ("npm ci", "npm run build")}
    assert idx["npm ci"] < idx["npm run build"]


def test_all_actions_sha_pinned(wf):
    for job in wf["jobs"].values():
        for s in _steps(job):
            if "uses" in s:
                assert SHA_RE.search(s["uses"]), s["uses"]


def test_python_gates_enforced(wf):
    runs = _runs(wf["jobs"]["test"]) + "\n" + _runs(wf["jobs"]["lint"])
    assert "--cov-fail-under=60" in runs
    assert "teken cli doctor . --strict" in runs


def test_sonar_gate_waits():
    text = WORKFLOW.read_text(encoding="utf-8")
    props = (ROOT / "sonar-project.properties").read_text(encoding="utf-8")
    assert "sonarqube-scan-action" in text
    assert "sonar.qualitygate.wait=true" in props


def test_playwright_port_env():
    cfg = (ROOT / "web" / "playwright.config.ts").read_text(encoding="utf-8")
    assert "PLAYWRIGHT_PORT" in cfg
    assert "4174" in cfg
