"""d26: the PR fixer's status comment, one per fix chain, edited live (StatusBoard).

The board is driven here on hand-built run documents of the shipped ``pr-fix`` workflow
(a chain of one run, started by an external event); chains of several runs, re-fixes and
the chain-end action run end to end in tests/rules/test_pr_fixer_status.py.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from culture_rules.apps.github import GitHubError
from culture_rules.apps.public_text import clean_block
from culture_rules.node.fixer_status import (
    EDIT_FLOOR_S,
    NOTES_EVERY_S,
    STATUS_COLLECTION,
    Chain,
    StatusBoard,
    render,
    status_actor,
)
from culture_rules.store.memory import MemoryStore

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "docs" / "rules" / "pr-fixer"
T0 = datetime(2026, 10, 9, 12, 0, 0, tzinfo=UTC)
REPO = "o/r"
SHA = "0123456789abcdef0123456789abcdef01234567"
KEY = f"pr-fixer:{REPO}#7"
GHP = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
RUN = "run-0123456789abcdef0123456789abcdef"


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
    """The App's issue-comment calls, recorded; ``fail_edit`` / ``fail_post`` raise."""

    def __init__(self) -> None:
        self.bodies: dict[int, str] = {}
        self.posts: list[tuple[str, int, str]] = []
        self.edits: list[tuple[str, int, str]] = []
        self.fail_edit: GitHubError | None = None
        self.fail_post: GitHubError | None = None
        self.next_id = 100

    def post_comment(self, repo, number, body):
        if self.fail_post is not None:
            raise self.fail_post
        self.next_id += 1
        self.posts.append((repo, number, body))
        self.bodies[self.next_id] = body
        return {"comment_id": self.next_id, "url": f"https://github.com/{repo}#c{self.next_id}"}

    def update_issue_comment(self, repo, comment_id, body):
        if self.fail_edit is not None:
            raise self.fail_edit
        self.edits.append((repo, comment_id, body))
        self.bodies[comment_id] = body
        return {"comment_id": comment_id, "url": "u"}


def steps(**status) -> list[dict]:
    """Step states of a pr-fix run: ``quiet``, ``secrets``, ``fix`` and its first try."""
    out = []
    for key in ("quiet", "secrets", "threads", "sonar", "fix", "fix[0]/agent", "fix[0]/gate"):
        out.append({"key": key, "status": status.get(key.replace("fix[0]/", ""), "pending")})
    return out


def fix_run(**kw) -> dict:
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
                "number": 7,
                "head_sha": SHA,
                "conclusion": "failure",
            },
        },
        "concurrency_key": KEY,
        "created_at": "2026-10-09T11:59:00Z",
        "finished_at": None,
        "outputs": None,
        "error": None,
        "steps": steps(quiet="succeeded", secrets="succeeded", agent="waiting"),
    }
    run.update(kw)
    return run


def set_steps(store, **status) -> None:
    run = store.get("runs", RUN)
    store.put("runs", {**run, "steps": steps(**status)})


@pytest.fixture
def world():
    store = MemoryStore()
    clock = Clock()
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    board = StatusBoard(store, clock=clock, sleep=sleep)
    issues = Issues()
    w = type("W", (), {})()
    w.store, w.clock, w.board, w.issues, w.slept = store, clock, board, issues, slept
    w.served = {"github-app"}
    w.tick = lambda: board.tick(lambda actor, repo: issues, lambda a: a in w.served)
    w.finish = lambda run, text: board.finish(lambda actor, repo: issues, run, text)
    return w


def record(w) -> dict:
    return w.store.get(STATUS_COLLECTION, RUN)


# --------------------------------------------------------------------------- opting in


def test_the_shipped_fixer_rules_write_the_status_comment():
    for name in ("pr-fixer-checks", "pr-fixer-refix", "pr-fixer-review-commit", "pr-fixer-publish"):
        assert status_actor(load("rules", name)) == "github-app"
    assert status_actor(load("rules", "pr-fixer-secrets")) is None  # d25: its own comment


def test_a_rule_without_status_writes_none(world):
    rule = load("rules", "pr-fixer-checks")
    for key in ("action", "on_failure"):
        rule[key]["params"].pop("status")
    world.store.put("runs", fix_run(rule={"id": "x", "definition": rule}))
    assert world.tick() == 0
    assert world.issues.posts == []


# --------------------------------------------------------------------------- the start


def test_nothing_is_posted_during_the_quiet_period_or_the_hold(world):
    world.store.put("runs", fix_run(steps=steps(quiet="sleeping")))
    world.tick()
    set_steps(world.store, quiet="succeeded", secrets="dispatching")
    world.tick()
    assert world.issues.posts == []
    assert record(world) is None


def test_past_the_hold_one_status_comment_is_posted_once(world):
    world.store.put("runs", fix_run())
    assert world.tick() == 1
    world.clock.advance(120)
    world.tick()
    (post,) = world.issues.posts
    repo, number, body = post
    assert (repo, number) == (REPO, 7)
    assert body.startswith("**PR fixer is working on this PR.**")
    assert "Started by checks settled (failure) at `0123456789ab`." in body
    assert "- **done** Quiet period and GitGuardian hold" in body
    assert "- **working (try 1 of 3)** Agent (qwen-fixer)" in body
    assert f"https://rules.culture.dev/api/runs/{RUN}" in body
    doc = record(world)
    assert doc["state"] == "posted"
    assert doc["comment_id"] == 101
    assert doc["final"] is False


def test_a_node_that_cannot_act_as_the_app_posts_nothing(world):
    world.served = set()
    world.store.put("runs", fix_run())
    assert world.tick() == 0
    assert world.issues.posts == []


def test_a_failed_post_is_recorded_and_never_retried_by_the_tick(world):
    world.issues.fail_post = GitHubError("http_502", retryable=True)
    world.store.put("runs", fix_run())
    world.tick()
    world.issues.fail_post = None
    world.clock.advance(120)
    world.tick()
    assert world.issues.posts == []
    assert record(world)["state"] == "unknown"


# --------------------------------------------------------------------------- live edits


def test_a_stage_change_edits_the_comment_after_the_floor(world):
    world.store.put("runs", fix_run())
    world.tick()
    set_steps(world.store, quiet="succeeded", secrets="succeeded", agent="succeeded")
    world.tick()
    assert world.issues.edits == []  # within the floor of the post
    world.clock.advance(EDIT_FLOOR_S)
    world.tick()
    (edit,) = world.issues.edits
    assert edit[1] == 101
    assert "- **done** Agent (qwen-fixer)" in edit[2]
    assert "- **working** Test gate" not in edit[2]
    world.clock.advance(EDIT_FLOOR_S)
    world.tick()
    assert len(world.issues.edits) == 1  # nothing changed since


def test_a_gate_verdict_is_shown(world):
    world.store.put("runs", fix_run())
    world.tick()
    run = world.store.get("runs", RUN)
    for st in run["steps"]:
        if st["key"] == "fix[0]/gate":
            st.update(status="succeeded", outputs={"verdict": "pass"})
    world.store.put("runs", run)
    world.clock.advance(EDIT_FLOOR_S)
    world.tick()
    assert "- **verdict pass** Test gate" in world.issues.edits[-1][2]


def add_note(w, text: str, at: datetime, seq: int = 1) -> None:
    doc = w.store.get("bridge_invocations", "bri_1") or {
        "id": "bri_1",
        "run_id": RUN,
        "attempt": 1,
        "status_notes": [],
    }
    notes = [*doc["status_notes"], {"at": at.strftime("%Y-%m-%dT%H:%M:%SZ"), "text": text}]
    w.store.put(
        "bridge_invocations",
        {**doc, "status_notes": notes, "last_event_at": notes[-1]["at"], "last_sequence": seq},
    )


def test_agent_notes_edit_at_most_once_a_minute(world):
    world.store.put("runs", fix_run())
    world.tick()
    world.clock.advance(EDIT_FLOOR_S)
    add_note(world, "reading the failing test", world.clock.now)
    world.tick()
    assert world.issues.edits == []  # notes only: once a minute
    world.clock.advance(NOTES_EVERY_S)
    world.tick()
    (edit,) = world.issues.edits
    assert "**Agent notes**" in edit[2]
    assert "- 12:00 UTC: reading the failing test" in edit[2]
    add_note(world, "fixed it, rerunning", world.clock.now, seq=2)
    world.clock.advance(10)
    world.tick()
    assert len(world.issues.edits) == 1
    world.clock.advance(NOTES_EVERY_S)
    world.tick()
    assert "fixed it, rerunning" in world.issues.edits[-1][2]


def test_a_deleted_comment_is_posted_again_and_followed(world):
    world.store.put("runs", fix_run())
    world.tick()
    world.issues.fail_edit = GitHubError("http_404")
    set_steps(world.store, quiet="succeeded", secrets="succeeded", agent="succeeded")
    world.clock.advance(EDIT_FLOOR_S)
    world.tick()
    assert len(world.issues.posts) == 2
    assert record(world)["comment_id"] == 102
    world.issues.fail_edit = None
    run = world.store.get("runs", RUN)
    world.store.put("runs", {**run, "status": "failed"})
    world.clock.advance(EDIT_FLOOR_S)
    world.finish(world.store.get("runs", RUN), "PR fixer handed back (actor_failed): x")
    assert world.issues.edits[-1][1] == 102


def test_a_failed_edit_is_tried_again_next_tick(world):
    world.store.put("runs", fix_run())
    world.tick()
    world.issues.fail_edit = GitHubError("http_502", retryable=True)
    set_steps(world.store, quiet="succeeded", secrets="succeeded", agent="succeeded")
    world.clock.advance(EDIT_FLOOR_S)
    world.tick()
    world.issues.fail_edit = None
    world.tick()
    assert len(world.issues.edits) == 1


# --------------------------------------------------------------------------- the end


def test_the_chain_end_text_lands_in_the_status_comment(world):
    world.store.put("runs", fix_run())
    world.tick()
    world.clock.advance(60)
    run = {**world.store.get("runs", RUN), "status": "failed"}
    world.store.put("runs", run)
    out = world.finish(run, "PR fixer handed back (actor_failed): no_changes\n\nRun: x")
    assert out == {"comment_id": 101, "url": "https://github.com/o/r#c101", "status": True}
    assert len(world.issues.posts) == 1  # no second comment
    body = world.issues.bodies[101]
    assert body.startswith("PR fixer handed back (actor_failed): no_changes")
    assert "**PR fixer status**" in body
    assert record(world)["final"] is True
    world.clock.advance(600)
    world.tick()
    assert len(world.issues.edits) == 1  # a final comment is never edited again


def test_the_final_edit_waits_out_the_floor(world):
    world.store.put("runs", fix_run())
    world.tick()
    world.clock.advance(2)
    world.finish(world.store.get("runs", RUN), "done")
    assert world.slept == [EDIT_FLOOR_S - 2]


def test_a_chain_without_a_status_comment_posts_its_final_one(world):
    run = fix_run(status="failed", steps=steps(quiet="succeeded", secrets="failed"))
    world.store.put("runs", run)
    world.finish(run, "PR fixer handed back (actor_failed): secrets_found")
    ((_, _, body),) = world.issues.posts
    assert body.startswith("PR fixer handed back (actor_failed): secrets_found")
    assert record(world)["final"] is True


def test_finish_outside_a_status_chain_is_none(world):
    rule = load("rules", "pr-fixer-checks")
    rule["on_failure"]["params"].pop("status")
    rule["action"]["params"].pop("status")
    run = fix_run(rule={"id": "x", "definition": rule})
    assert world.finish(run, "x") is None
    assert world.issues.posts == []


def test_a_failed_final_edit_is_reported(world):
    world.store.put("runs", fix_run())
    world.tick()
    world.issues.fail_edit = GitHubError("http_502", retryable=True)
    out = world.finish(world.store.get("runs", RUN), "done")
    assert out == {"error": "http_502", "retryable": True}


def test_a_cancelled_chain_is_closed_by_the_tick(world):
    world.store.put("runs", fix_run())
    world.tick()
    run = world.store.get("runs", RUN)
    world.store.put("runs", {**run, "status": "cancelled", "finished_at": "2026-10-09T12:00:01Z"})
    world.clock.advance(EDIT_FLOOR_S)
    world.tick()
    assert world.issues.edits[-1][2].startswith("**PR fixer stopped:** the run was cancelled.")
    assert record(world)["final"] is True


def test_a_chain_idle_after_its_last_run_is_closed(world):
    world.store.put("runs", fix_run())
    world.tick()
    run = world.store.get("runs", RUN)
    world.store.put("runs", {**run, "status": "succeeded", "finished_at": "2026-10-09T12:00:01Z"})
    world.clock.advance(60)
    world.tick()
    assert record(world)["final"] is False  # a continuation may still come
    world.clock.advance(15 * 60)
    world.tick()
    assert "**PR fixer: the chain ended** (last run succeeded)" in world.issues.edits[-1][2]
    assert record(world)["final"] is True


# --------------------------------------------------------------------------- what is relayed


def test_relayed_text_is_cleaned_and_engine_facts_validated():
    run = fix_run(
        status="succeeded",
        outputs={"summary": f"fixed it @mallory, see https://evil.example and {GHP}"},
        trigger={
            "id": "ev",
            "type": "github.comment.created",
            "data": {
                "repository": REPO,
                "number": 7,
                "head_sha": "not-a-sha <b>",
                "author": "@everyone <script>",
            },
        },
    )
    chain = Chain(root=run, runs=[run], notes=[{"at": "2026-10-09T12:00:00Z", "text": "ok"}])
    body = render(chain)
    assert "@mallory" not in body
    assert "evil.example" not in body
    assert GHP not in body
    assert "<script>" not in body
    assert "not-a-sha" not in body
    assert "Started by a comment." in body
    assert "- 12:00 UTC: ok" in body


def test_a_final_body_from_the_action_is_cleaned():
    run = fix_run()
    final_text = "handed back: @someone <img src=x> " + GHP
    body = render(Chain(root=run, runs=[run]), final=clean_block(final_text))
    assert "@someone" not in body
    assert "<img" not in body
    assert GHP not in body


def test_the_render_of_a_chain_is_stable():
    run = fix_run()
    chain = Chain(root=run, runs=[copy.deepcopy(run)])
    assert render(chain) == render(chain)
