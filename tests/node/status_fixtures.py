"""Shared doubles for the d26 status comment tests: hand-built pr-fix runs and a fake App."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from culture_rules.apps.github import GitHubError
from tests.rules.chain_world import plain  # noqa: F401 - re-exported for the tests

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "pr-fixer"
T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
REPO = "o/r"
SHA = "0123456789abcdef0123456789abcdef01234567"
KEY = f"pr-fixer:{REPO}#7"
RUN = "run-0123456789abcdef0123456789abcdef"
APP_ID = "1"


def load(kind: str, name: str) -> dict:
    return json.loads((BUNDLE / kind / f"{name}.json").read_text())


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Issues:
    """The App's issue-comment calls, recorded. ``fail_edit`` / ``fail_post`` raise (a list
    raises its items one call at a time); ``lose_post`` creates the comment, then raises
    (an accepted post whose answer is lost); ``deleted`` comments answer 404."""

    app_id = APP_ID

    def __init__(self) -> None:
        self.bodies: dict[int, str] = {}
        self.posts: list[tuple[str, int, str]] = []
        self.edits: list[tuple[str, int, str]] = []
        self.listed = 0
        self.fail_edit: list[GitHubError] = []
        self.fail_post: list[GitHubError] = []
        self.lose_post = False
        self.deleted: set[int] = set()
        self.others: list[dict] = []  # comments by someone else
        self.next_id = 100

    def post_comment(self, repo, number, body):
        if self.fail_post:
            raise self.fail_post.pop(0)
        self.next_id += 1
        self.posts.append((repo, number, body))
        self.bodies[self.next_id] = body
        if self.lose_post:
            self.lose_post = False
            raise GitHubError("http_502", retryable=True)
        return {"comment_id": self.next_id, "url": f"https://github.com/{repo}#c{self.next_id}"}

    def update_issue_comment(self, repo, comment_id, body):
        if comment_id in self.deleted:
            raise GitHubError("http_404")
        if self.fail_edit:
            raise self.fail_edit.pop(0)
        self.edits.append((repo, comment_id, body))
        self.bodies[comment_id] = body
        return {"comment_id": comment_id, "url": "u"}

    def list_issue_comments(self, repo, number):
        self.listed += 1
        mine = [
            {"comment_id": n, "url": f"u{n}", "body": b, "app_id": self.app_id}
            for n, b in sorted(self.bodies.items())
            if n not in self.deleted
        ]
        return [*self.others, *mine]


def steps(**status) -> list[dict]:
    """Step states of a pr-fix run: ``quiet``, ``secrets``, ``fix`` and its first try."""
    out = []
    for key in ("quiet", "secrets", "threads", "sonar", "fix", "fix[0]/agent", "fix[0]/gate"):
        out.append({"key": key, "status": status.get(key.replace("fix[0]/", ""), "pending")})
    return out


def fix_run(**kw) -> dict:
    number = kw.pop("number", 7)
    run = {
        "id": RUN,
        "status": "running",
        "rule_id": "pr-fixer-checks",
        "rule": {"id": "pr-fixer-checks", "definition": load("rules", "pr-fixer-checks")},
        "workflow_id": "pr-fix",
        "workflow": {"id": "pr-fix", "definition": load("workflows", "pr-fix")},
        "trigger": {
            "id": "ev-1",
            "type": "github.pr.checks_settled",
            "data": {
                "repository": REPO,
                "number": number,
                "head_sha": SHA,
                "conclusion": "failure",
            },
        },
        "concurrency_key": f"pr-fixer:{REPO}#{number}",
        "created_at": "2026-10-09T11:59:00+00:00",
        "finished_at": None,
        "outputs": None,
        "error": None,
        "steps": steps(quiet="succeeded", secrets="succeeded", agent="waiting"),
    }
    run.update(kw)
    return run


def set_steps(store, run_id: str = RUN, **status) -> None:
    run = store.get("runs", run_id)
    store.put("runs", {**run, "steps": steps(**status)})


def set_run(store, run_id: str = RUN, **fields) -> None:
    run = store.get("runs", run_id)
    store.put("runs", {**run, **fields})
