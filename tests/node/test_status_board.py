"""d26: the status board - one comment per chain, leased, acknowledged, paced.

Every write is a compare-and-set; a board posts, edits or recreates only under the
record's lease; a lost post is resolved by listing the PR's comments; the chain-end text is
a pending final, marked final only once GitHub took it; failures back off and permanent
refusals give up; a tick has a call budget.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from culture_rules.apps.github import GitHubError
from culture_rules.node.fixer_status import (
    EDIT_FLOOR_S,
    NOTES_EVERY_S,
    STATUS_COLLECTION,
    marker_of,
)
from culture_rules.node.status_board import (
    BACKOFF_CAP,
    FINAL_HORIZON,
    LEASE,
    RETENTION,
    StatusBoard,
    ensure_status_indexes,
)
from culture_rules.store.memory import MemoryStore
from tests.node.status_fixtures import (
    APP_ID,
    REPO,
    RUN,
    Clock,
    Issues,
    fix_run,
    load,
    plain,
    set_run,
    set_steps,
    steps,
)

GHP = "ghp" + "_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
RUN2 = "run-fedcba9876543210fedcba9876543210"
HANDED_BACK = "PR fixer handed back (actor_failed): no_changes"


class World:
    def __init__(self) -> None:
        self.store = MemoryStore()
        self.clock = Clock()
        self.issues = Issues()
        self.served = {"github-app"}
        self.board = self.new_board("a")

    def new_board(self, owner: str, **kw) -> StatusBoard:
        return StatusBoard(self.store, clock=self.clock, owner=owner, known=lambda: (), **kw)

    def tick(self, board: StatusBoard | None = None) -> int:
        return (board or self.board).tick(
            lambda actor, repo: self.issues, lambda actor: actor in self.served
        )

    def finish(self, run: dict, text: str = HANDED_BACK) -> dict | None:
        return self.board.finish(run, text, where=(REPO, run["trigger"]["data"]["number"]))

    def record(self, run_id: str = RUN) -> dict:
        return self.store.get(STATUS_COLLECTION, run_id)

    def run(self, run_id: str = RUN) -> dict:
        return self.store.get("runs", run_id)

    def later(self, seconds: float = EDIT_FLOOR_S) -> None:
        self.clock.advance(seconds)

    def after_retry(self, run_id: str = RUN) -> None:
        from culture_rules.node.fixer_status import parse_time

        at = parse_time(self.record(run_id)["retry_at"])
        self.clock.now = max(self.clock.now, at or self.clock.now) + timedelta(seconds=1)


@pytest.fixture
def w() -> World:
    return World()


def started(w: World) -> None:
    w.store.put("runs", fix_run())
    w.tick()


# --------------------------------------------------------------------------- the start


def test_nothing_is_posted_during_the_quiet_period_or_the_hold(w):
    w.store.put("runs", fix_run(steps=steps(quiet="sleeping")))
    w.tick()
    set_steps(w.store, quiet="succeeded", secrets="dispatching")
    w.tick()
    assert w.issues.posts == []
    assert w.record() is None


def test_past_the_hold_one_status_comment_is_posted_once(w):
    started(w)
    w.later(120)
    w.tick()
    (post,) = w.issues.posts
    assert post[:2] == (REPO, 7)
    assert post[2].endswith(marker_of(RUN))
    doc = w.record()
    assert doc["state"] == "posted"
    assert doc["comment_id"] == 101
    assert doc["lease"] is None  # released after the work


def test_a_rule_without_status_writes_none(w):
    rule = load("rules", "pr-fixer-checks")
    for key in ("action", "on_failure"):
        rule[key]["params"].pop("status")
    w.store.put("runs", fix_run(rule={"id": "x", "definition": rule}))
    w.tick()
    assert w.issues.posts == []


def test_a_node_that_cannot_act_as_the_app_posts_nothing(w):
    w.served = set()
    started(w)
    assert w.issues.posts == []


# --------------------------------------------------------------------------- live edits


def test_a_stage_change_is_edited_in_after_the_floor(w):
    started(w)
    set_steps(w.store, quiet="succeeded", secrets="succeeded", agent="succeeded")
    w.tick()
    assert w.issues.edits == []
    w.later()
    w.tick()
    (edit,) = w.issues.edits
    assert "- **done** Agent (qwen-fixer)" in edit[2]
    w.later()
    w.tick()
    assert len(w.issues.edits) == 1  # nothing changed since


def add_note(w: World, text: str) -> None:
    doc = w.store.get("bridge_invocations", "bri_1") or {
        "id": "bri_1",
        "run_id": RUN,
        "attempt": 1,
        "status_notes": [],
    }
    at = w.clock.now.strftime("%Y-%m-%dT%H:%M:%SZ")
    notes = [*doc["status_notes"], {"at": at, "text": text}]
    w.store.put("bridge_invocations", {**doc, "status_notes": notes, "last_event_at": at})


def test_agent_notes_alone_edit_at_most_once_a_minute(w):
    started(w)
    w.later()
    add_note(w, "reading the failing test")
    w.tick()
    assert w.issues.edits == []
    w.later(NOTES_EVERY_S)
    w.tick()
    assert "reading the failing test" in w.issues.edits[-1][2]


# --------------------------------------------------------------------------- one writer


def test_a_deleted_comment_is_recreated_once_across_two_boards(w):
    started(w)
    other = w.new_board("b")
    w.issues.deleted.add(101)
    set_steps(w.store, quiet="succeeded", secrets="succeeded", agent="succeeded")
    w.later()
    w.tick()
    w.tick(other)
    w.later()
    w.tick(other)
    assert len(w.issues.posts) == 2
    assert w.record()["comment_id"] == 102


def test_a_board_without_the_lease_does_nothing(w):
    started(w)
    other = w.new_board("b")
    doc = w.record()
    until = (w.clock.now + LEASE).strftime("%Y-%m-%dT%H:%M:%SZ")
    w.store.put(STATUS_COLLECTION, {**doc, "lease": {"owner": "a", "until": until}})
    set_steps(w.store, quiet="succeeded", secrets="succeeded", agent="succeeded")
    w.later()
    w.tick(other)
    assert w.issues.edits == []
    w.later(LEASE.total_seconds())
    w.tick(other)  # an expired lease is taken over
    assert len(w.issues.edits) == 1


class Racing(MemoryStore):
    """Another writer moves the record right before this board's next write."""

    def __init__(self) -> None:
        super().__init__()
        self.race = False

    def update_if(self, collection, doc_id, expected, changes):
        if self.race and collection == STATUS_COLLECTION and changes.get("state") == "posting":
            self.race = False
            current = self.get(collection, doc_id)
            super().update_if(collection, doc_id, {"rev": current["rev"]}, {"rev": 99})
        return super().update_if(collection, doc_id, expected, changes)


def test_a_lost_compare_and_set_stops_before_any_call():
    w = World()
    w.store = Racing()
    w.board = w.new_board("a")
    w.store.race = True
    started(w)
    assert w.issues.posts == []
    assert w.record()["state"] == "none"
    w.tick()
    assert len(w.issues.posts) == 1  # the next tick re-reads and posts


# --------------------------------------------------------------------------- lost posts


def test_an_accepted_post_whose_answer_was_lost_is_adopted(w):
    w.issues.lose_post = True
    started(w)
    assert w.record()["state"] == "unknown"
    w.after_retry()
    w.tick()
    assert w.issues.listed == 1
    assert w.record()["state"] == "posted"
    assert w.record()["comment_id"] == 101
    set_steps(w.store, quiet="succeeded", secrets="succeeded", agent="succeeded")
    w.later()
    w.tick()
    assert len(w.issues.posts) == 1  # never posted twice
    assert w.issues.edits[-1][1] == 101


def test_a_lost_post_that_created_nothing_gives_up_silently(w):
    w.issues.fail_post = [GitHubError("http_502", retryable=True)]
    started(w)
    w.after_retry()
    w.tick()
    doc = w.record()
    assert doc["state"] == "unresolved"
    assert doc["final"] is True
    w.later(600)
    w.tick()
    assert w.issues.posts == []


def test_only_this_apps_comment_with_this_chains_marker_is_adopted(w):
    w.issues.others = [
        {"comment_id": 7, "url": "x", "body": marker_of(RUN), "app_id": "999"},
        {"comment_id": 8, "url": "y", "body": marker_of(RUN2), "app_id": APP_ID},
    ]
    w.issues.fail_post = [GitHubError("http_502", retryable=True)]
    started(w)
    w.after_retry()
    w.tick()
    assert w.record()["state"] == "unresolved"


# --------------------------------------------------------------------------- the end


def test_the_chain_end_is_a_pending_final_delivered_by_the_tick(w):
    started(w)
    w.later(60)
    set_run(w.store, status="failed")
    out = w.finish(w.run())
    assert out == {"status": True, "pending": True}
    assert w.issues.edits == []  # the action never calls GitHub
    assert w.record()["final"] is False
    w.tick()
    assert len(w.issues.posts) == 1
    assert plain(w.issues.bodies[101]).startswith(HANDED_BACK)
    assert w.record()["final"] is True
    assert w.record()["outcome"] == "delivered"


def test_a_final_is_retried_with_backoff_until_github_takes_it(w):
    started(w)
    w.later(60)
    w.finish(w.run())
    w.issues.fail_edit = [GitHubError("http_502", retryable=True)] * 2
    w.tick()
    first = w.record()
    assert first["final"] is False
    assert first["failures"] == 1
    w.tick()
    assert w.record()["failures"] == 1  # not before retry_at
    w.after_retry()
    w.tick()
    assert w.record()["failures"] == 2
    w.after_retry()
    w.tick()
    assert w.record()["final"] is True
    assert w.record()["failures"] == 0


def test_the_backoff_is_capped(w):
    started(w)
    w.finish(w.run())
    w.issues.fail_edit = [GitHubError("http_502", retryable=True)] * 20
    for _ in range(15):
        w.after_retry()
        w.tick()
    from culture_rules.node.fixer_status import parse_time

    waited = parse_time(w.record()["retry_at"]) - w.clock.now
    assert waited <= BACKOFF_CAP


def test_a_permanent_refusal_gives_up_after_three_tries(w):
    started(w)
    w.finish(w.run())
    w.issues.fail_edit = [GitHubError("http_403")] * 5
    for _ in range(6):
        w.later()
        w.after_retry()
        w.tick()
    doc = w.record()
    assert doc["final"] is True
    assert doc["outcome"] == "gave_up"
    assert doc["failures"] == 3


def test_a_final_never_delivered_gives_up_at_the_horizon(w):
    started(w)
    w.finish(w.run())
    w.issues.fail_edit = [GitHubError("http_502", retryable=True)] * 100
    w.clock.advance(FINAL_HORIZON.total_seconds() + 1)
    w.tick()
    assert w.record()["outcome"] == "gave_up"


def test_a_chain_without_a_comment_posts_its_final_once(w):
    run = fix_run(status="failed", steps=steps(quiet="succeeded", secrets="failed"))
    w.store.put("runs", run)
    assert w.finish(run) == {"status": True, "pending": True}
    w.tick()
    w.later(60)
    w.tick()
    ((_, _, body),) = w.issues.posts
    assert plain(body).startswith(HANDED_BACK)


def test_the_action_succeeds_even_when_nothing_can_be_posted(w):
    run = fix_run(status="failed")
    w.store.put("runs", run)
    w.issues.fail_post = [GitHubError("http_403")] * 5
    assert w.finish(run)["status"] is True
    for _ in range(4):
        w.tick()
        w.after_retry()
    assert w.record()["outcome"] == "gave_up"
    assert w.issues.posts == []


def test_finish_outside_a_status_chain_is_none(w):
    rule = load("rules", "pr-fixer-checks")
    for key in ("action", "on_failure"):
        rule[key]["params"].pop("status")
    assert w.finish(fix_run(rule={"id": "x", "definition": rule})) is None


def test_a_cancelled_chain_is_closed_after_the_floor(w):
    started(w)
    set_run(w.store, status="cancelled", finished_at="2026-10-09T12:00:01+00:00")
    w.tick()
    assert w.issues.edits == []  # the floor holds for closings too
    w.later()
    w.tick()
    assert plain(w.issues.edits[-1][2]).startswith("PR fixer stopped: the run was cancelled.")
    assert w.record()["final"] is True


def test_a_chain_idle_after_its_last_run_is_closed(w):
    started(w)
    set_run(w.store, status="succeeded", finished_at="2026-10-09T12:00:01+00:00")
    w.later(60)
    w.tick()
    assert w.record()["final"] is False
    w.later(15 * 60)
    w.tick()
    assert plain(w.issues.edits[-1][2]).startswith("PR fixer: the chain ended.")


# --------------------------------------------------------------------------- budget, housekeeping


def test_a_tick_keeps_to_its_call_budget(w):
    w.board = w.new_board("a", max_calls=1)
    w.store.put("runs", fix_run())
    w.store.put("runs", fix_run(id=RUN2, number=8))
    w.tick()
    assert len(w.issues.posts) == 1
    w.tick()
    assert len(w.issues.posts) == 2


def test_old_final_records_are_dropped(w):
    old = (w.clock.now - RETENTION - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    young = (w.clock.now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"final": True, "created_at": young, "rev": 0}
    w.store.put(STATUS_COLLECTION, {**base, "id": "old", "final_at": old})
    w.store.put(STATUS_COLLECTION, {**base, "id": "young", "final_at": young})
    w.tick()
    assert w.store.get(STATUS_COLLECTION, "old") is None
    assert w.store.get(STATUS_COLLECTION, "young") is not None


def test_the_board_declares_its_indexes():
    calls = []

    class Indexed:
        def ensure_index(self, collection, keys, *, name):
            calls.append((collection, name))

    ensure_status_indexes(Indexed())
    assert ("runs", "runs_key_created") in calls
    assert ("bridge_invocations", "bridge_by_run") in calls
    assert (STATUS_COLLECTION, "status_open") in calls


def test_a_known_secret_never_reaches_github(w):
    secret = "synthetic-" + "node-secret-0042"
    w.board = StatusBoard(w.store, clock=w.clock, owner="a", known=lambda: {secret})
    run = fix_run(status="failed")
    w.store.put("runs", run)
    w.finish(run, f"handed back: {secret[::1]} and {GHP}")
    w.tick()
    ((_, _, body),) = w.issues.posts
    assert secret not in body
    assert GHP not in body
    assert body.startswith("[withheld]")
