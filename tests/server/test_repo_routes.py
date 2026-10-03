"""GET /repos and repo-backed /export and /import through culture_rules/io/gitrepo.py (t42).

Every repository is local to ``tmp_path``: a bare "origin" and a working clone of it. Nothing
here touches a remote host.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from culture_rules.engine.audit import AUDIT_COLLECTION  # noqa: E402
from culture_rules.server.repos import RepoTarget, parse_repos, repos_from_env  # noqa: E402
from tests.server.conftest import ALICE, dev_app, rule_body  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs the git CLI")

_ID = ("-c", "user.name=test", "-c", "user.email=test@localhost")


def git(cwd: Path, *argv: str) -> str:
    done = subprocess.run(  # noqa: S603
        ["git", *_ID, *argv], cwd=cwd, capture_output=True, text=True, check=True  # noqa: S607
    )
    return done.stdout.strip()


@pytest.fixture
def repos(tmp_path):
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(bare))
    work = tmp_path / "work"
    git(tmp_path, "clone", "--quiet", str(bare), str(work))
    git(work, "checkout", "--quiet", "-b", "main")
    (work / "README").write_text("defs\n", encoding="utf-8")
    git(work, "add", "README")
    git(work, "commit", "--quiet", "-m", "init")
    git(work, "push", "--quiet", "origin", "main")
    return bare, work


@pytest.fixture
def app_client(store, repos):
    bare, work = repos
    targets = [RepoTarget("work", str(work)), RepoTarget("origin", str(bare))]
    return TestClient(dev_app(store, repos=targets))


# --------------------------------------------------------------------------- config


def test_parse_repos_names_entries_and_skips_blanks():
    parsed = parse_repos("defs=/srv/defs, ,https://github.com/agentculture/workflows.git\n/x/y")
    assert parsed == [
        RepoTarget("defs", "/srv/defs"),
        RepoTarget("agentculture/workflows", "https://github.com/agentculture/workflows.git"),
        RepoTarget("y", "/x/y"),
    ]
    assert parse_repos("") == []
    assert repos_from_env({}) == []
    assert repos_from_env({"CULTURE_RULES_REPOS": "a=/tmp/a"}) == [RepoTarget("a", "/tmp/a")]


def test_get_repos_lists_configured_targets(app_client, repos):
    bare, work = repos
    items = app_client.get("/repos").json()["items"]
    assert items == [
        {"name": "work", "url": str(work), "writable": True},
        {"name": "origin", "url": str(bare), "writable": False},
    ]


def test_get_repos_defaults_to_none(store, monkeypatch):
    monkeypatch.delenv("CULTURE_RULES_REPOS", raising=False)
    assert TestClient(dev_app(store)).get("/repos").json() == {"items": []}


def test_get_repos_reads_the_environment(store, monkeypatch, tmp_path):
    monkeypatch.setenv("CULTURE_RULES_REPOS", f"lab={tmp_path}")
    items = TestClient(dev_app(store)).get("/repos").json()["items"]
    assert [i["name"] for i in items] == ["lab"]


# --------------------------------------------------------------------------- export


def test_export_to_repo_is_a_dry_run_by_default(app_client, repos):
    _, work = repos
    head = git(work, "rev-parse", "HEAD")
    assert app_client.post("/rules", json=rule_body("r1"), headers=ALICE).status_code == 201

    r = app_client.post("/export", json={"repo": "work"}, headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo"] == "work" and body["applied"] is False and body["committed"] is False
    assert body["changes"] == [
        {"kind": "rules", "id": "r1", "path": "rules/r1.json", "action": "add"}
    ]
    assert git(work, "rev-parse", "HEAD") == head
    assert not (work / "rules").exists()


def test_export_to_repo_apply_commits_and_push_reaches_the_bare_origin(app_client, store, repos):
    bare, work = repos
    app_client.post("/rules", json=rule_body("r1"), headers=ALICE)
    before = len(store.find(AUDIT_COLLECTION))

    r = app_client.post("/export", json={"repo": "work", "apply": True}, headers=ALICE)
    body = r.json()
    assert r.status_code == 200 and body["applied"] is True and body["committed"] is True
    assert body["pushed"] is False
    assert body["commit"] == git(work, "rev-parse", "HEAD")
    assert json.loads((work / "rules" / "r1.json").read_text())["id"] == "r1"
    entries = store.find(AUDIT_COLLECTION)[before:]
    assert [e["verb"] for e in entries] == ["definitions.export_repo"]
    assert entries[0]["identity"] == "alice"

    again = app_client.post("/export", json={"repo": "work", "apply": True}, headers=ALICE)
    assert again.json()["committed"] is False  # nothing changed, no empty commit
    assert [c["action"] for c in again.json()["changes"]] == ["unchanged"]

    pushed = app_client.post(
        "/export", json={"repo": "work", "apply": True, "push": True}, headers=ALICE
    )
    assert pushed.json()["pushed"] is True
    assert git(bare, "rev-parse", "main") == git(work, "rev-parse", "HEAD")


def test_export_refuses_unknown_and_non_local_repos(app_client):
    r = app_client.post("/export", json={"repo": "nope"}, headers=ALICE)
    assert r.status_code == 404 and r.json()["error"]["code"] == "repo_not_found"
    r = app_client.post("/export", json={"repo": "origin"}, headers=ALICE)
    assert r.status_code == 422 and r.json()["error"]["code"] == "repo_not_local"
    r = app_client.post("/export", json={"repo": "work", "directory": "../out"}, headers=ALICE)
    assert r.status_code == 422


# --------------------------------------------------------------------------- import


def _seed_origin(work: Path) -> None:
    (work / "rules").mkdir()
    (work / "rules" / "r2.json").write_text(json.dumps(rule_body("r2")), encoding="utf-8")
    git(work, "add", "rules")
    git(work, "commit", "--quiet", "-m", "add r2")
    git(work, "push", "--quiet", "origin", "main")


def test_import_from_repo_is_a_dry_run_until_applied(app_client, store, repos):
    _, work = repos
    _seed_origin(work)

    plan = app_client.post("/import", json={"repo": "origin"}, headers=ALICE)
    assert plan.status_code == 200, plan.text
    assert plan.json()["applied"] is False
    assert [(c["path"], c["action"]) for c in plan.json()["changes"]] == [("rules/r2", "add")]
    assert store.get("rules", "r2") is None

    done = app_client.post("/import", json={"repo": "origin", "apply": True}, headers=ALICE)
    assert done.json()["applied"] is True
    assert store.get("rules", "r2")["id"] == "r2"


def test_import_needs_exactly_one_source(app_client):
    both = app_client.post("/import", json={"repo": "origin", "files": {}}, headers=ALICE)
    assert both.status_code == 422
    neither = app_client.post("/import", json={}, headers=ALICE)
    assert neither.status_code == 422
    missing = app_client.post("/import", json={"repo": "nope"}, headers=ALICE)
    assert missing.status_code == 404


def test_import_from_repo_reports_invalid_files(app_client, repos):
    _, work = repos
    (work / "rules").mkdir()
    (work / "rules" / "bad.json").write_text('{"id": "bad"}', encoding="utf-8")
    git(work, "add", "rules")
    git(work, "commit", "--quiet", "-m", "bad")
    r = app_client.post("/import", json={"repo": "work"}, headers=ALICE)
    assert r.status_code == 422
    assert r.json()["error"]["errors"]
