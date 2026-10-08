"""d26: the status board - a single writer reconciling each comment to its desired state.

Only the node on the App actor's machine writes. Each tick it renders the body the store's
inputs describe and sends it when GitHub has not acknowledged that body yet; ``acked_rev``
moves only on a 2xx; ambiguous failures are retried with backoff, so the newest desired
body always wins. Posting stays at most once (a lost answer is resolved by marker). Every
pending record ends within its horizons. Selection is per machine, due-first, paged.
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
    parse_time,
)
from culture_rules.node.status_board import (
    BACKOFF_CAP,
    CALL_DEADLINE_S,
    FINAL_HORIZON,
    IDLE_HORIZON,
    RETENTION,
    StatusBoard,
    ensure_status_indexes,
)
from culture_rules.store.memory import MemoryStore
from tests.node.status_fixtures import (
    APP_ACTOR,
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
NEWER = "PR fixer pushed the fix: gate pass"


class World:
    def __init__(self, store: MemoryStore | None = None) -> None:
        self.store = store or MemoryStore()
        self.store.put("actors", dict(APP_ACTOR))
        self.clock = Clock()
        self.issues = Issues()
        self.board = self.new_board()

    def new_board(self, **kw) -> StatusBoard:
        return StatusBoard(self.store, clock=self.clock, known=lambda: (), **kw)

    def tick(self, host: str = "spark") -> int:
        return self.board.tick(lambda actor, repo, until: self.issues, host)

    def finish(self, run: dict, text: str = HANDED_BACK) -> dict | None:
        return self.board.finish(run, text, where=(REPO, run["trigger"]["data"]["number"]))

    def record(self, run_id: str = RUN) -> dict:
        return self.store.get(STATUS_COLLECTION, run_id)

    def run(self, run_id: str = RUN) -> dict:
        return self.store.get("runs", run_id)

    def later(self, seconds: float = EDIT_FLOOR_S) -> None:
        self.clock.advance(seconds)

    def after_retry(self, run_id: str = RUN) -> None:
        at = parse_time(self.record(run_id)["retry_at"])
        self.clock.now = max(self.clock.now, at or self.clock.now) + timedelta(seconds=1)

    def shown(self, comment_id: int = 101) -> str:
        return plain(self.issues.bodies[comment_id])


@pytest.fixture
def w() -> World:
    return World()


def started(w: World) -> None:
    w.store.put("runs", fix_run())
    w.tick()


def agent_done(w: World) -> None:
    set_steps(w.store, quiet="succeeded", secrets="succeeded", agent="succeeded")


# --------------------------------------------------------------------------- the single writer


def test_nothing_is_posted_during_the_quiet_period_or_the_hold(w):
    w.store.put("runs", fix_run(steps=steps(quiet="sleeping")))
    w.tick()
    set_steps(w.store, quiet="succeeded", secrets="dispatching")
    w.tick()
    assert w.issues.posts == []
    assert w.record() is None


def test_past_the_hold_one_status_comment_is_posted_and_acknowledged(w):
    started(w)
    w.later(120)
    w.tick()
    (post,) = w.issues.posts
    assert post[:2] == (REPO, 7)
    assert post[2].endswith(marker_of(RUN))
    doc = w.record()
    assert doc["machine"] == "spark"
    assert doc["state"] == "posted"
    assert doc["acked_rev"] == doc["desired_rev"]


def test_a_node_not_on_the_apps_machine_never_writes(w):
    w.store.put("runs", fix_run())
    w.tick("spark2")
    assert w.record() is None
    w.tick()  # spark claims it
    w.later(60)
    agent_done(w)
    w.tick("spark2")
    assert w.issues.edits == []


def test_an_app_without_a_machine_has_no_live_status(w):
    w.store.put("actors", {**APP_ACTOR, "machine": None})
    run = fix_run()
    w.store.put("runs", run)
    w.tick()
    assert w.record() is None
    assert w.finish(run) is None  # the action then posts a plain comment


def test_a_rule_without_status_writes_none(w):
    rule = load("rules", "pr-fixer-checks")
    for key in ("action", "on_failure"):
        rule[key]["params"].pop("status")
    w.store.put("runs", fix_run(rule={"id": "x", "definition": rule}))
    w.tick()
    assert w.issues.posts == []


# --------------------------------------------------------------------------- desired state


def test_a_stage_change_is_sent_after_the_floor_and_nothing_else_is(w):
    started(w)
    agent_done(w)
    w.tick()
    assert w.issues.edits == []
    w.later()
    w.tick()
    (edit,) = w.issues.edits
    assert "- **done** Agent (qwen-fixer)" in edit[2]
    w.later()
    w.tick()
    assert len(w.issues.edits) == 1  # acknowledged: nothing to send


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


def test_agent_notes_alone_are_sent_at_most_once_a_minute(w):
    started(w)
    w.later()
    add_note(w, "reading the failing test")
    w.tick()
    assert w.issues.edits == []
    w.later(NOTES_EVERY_S)
    w.tick()
    assert "reading the failing test" in w.issues.edits[-1][2]


def test_an_ambiguous_edit_is_resent_and_acknowledged_only_on_a_2xx(w):
    started(w)
    agent_done(w)
    w.later()
    w.issues.fail_edit = [GitHubError("http_502", retryable=True)]
    w.tick()
    doc = w.record()
    assert doc["acked_rev"] != doc["desired_rev"]
    assert doc["failures"] == 1
    w.after_retry()
    w.tick()
    doc = w.record()
    assert doc["acked_rev"] == doc["desired_rev"]
    assert doc["failures"] == 0


# --------------------------------------------------------------------------- Codex's interleavings


def test_a_lost_response_to_a_working_edit_then_the_final_ends_final(w):
    """The working edit lands but its answer is lost; meanwhile the chain ends. The next
    try sends the current desired body - the final - and only that is acknowledged."""
    started(w)
    agent_done(w)
    w.later()

    def chain_ends():
        set_run(w.store, status="failed")
        w.finish(w.run())

    w.issues.on_edit = chain_ends
    w.issues.lose_edit = True
    w.tick()
    assert not w.shown().startswith(HANDED_BACK)  # the stale working body is up for now
    assert w.record()["final"] is False
    w.after_retry()
    w.tick()
    assert w.shown().startswith(HANDED_BACK)
    doc = w.record()
    assert doc["final"] is True
    assert doc["acked_rev"] == doc["desired_rev"]


def test_an_older_final_never_outlives_a_newer_one(w):
    """Final A is being sent when final B is stored (its acknowledging write is lost to
    B's): the next tick sends B, and B is what GitHub shows, acknowledged."""
    started(w)
    w.later(60)
    w.finish(w.run(), HANDED_BACK)
    w.issues.on_edit = lambda: w.finish(w.run(), NEWER)
    w.tick()
    assert w.shown().startswith(HANDED_BACK)
    assert w.record()["final"] is False  # A's acknowledgement lost to B's write
    w.later()
    w.tick()
    assert w.shown().startswith(NEWER)
    doc = w.record()
    assert doc["final"] is True
    assert doc["acked_rev"] == doc["desired_rev"]


def test_a_final_asked_while_an_edit_is_in_flight_is_sent_next(w):
    started(w)
    agent_done(w)
    w.later()

    def chain_ends():
        set_run(w.store, status="failed")
        w.finish(w.run())

    w.issues.on_edit = chain_ends  # the edit succeeds; the final arrives during it
    w.tick()
    assert w.record()["final"] is False
    w.later()
    w.tick()
    assert w.shown().startswith(HANDED_BACK)
    assert w.record()["outcome"] == "delivered"


# --------------------------------------------------------------------------- posting at most once


def test_a_deleted_comment_is_posted_again_once(w):
    started(w)
    w.issues.deleted.add(101)
    agent_done(w)
    w.later()
    w.tick()
    w.later()
    w.tick()
    assert len(w.issues.posts) == 2
    assert w.record()["comment_id"] == 102


class Racing(MemoryStore):
    """Another writer moves the record right before the board records ``posting``."""

    race = False

    def update_if(self, collection, doc_id, expected, changes):
        if self.race and collection == STATUS_COLLECTION and changes.get("state") == "posting":
            self.race = False
            current = self.get(collection, doc_id)
            super().update_if(collection, doc_id, {"rev": current["rev"]}, {"rev": 99})
        return super().update_if(collection, doc_id, expected, changes)


def test_a_lost_compare_and_set_stops_before_any_call():
    w = World(Racing())
    w.store.race = True
    started(w)
    assert w.issues.posts == []
    assert w.record()["state"] == "none"
    w.tick()
    assert len(w.issues.posts) == 1


def test_an_accepted_post_whose_answer_was_lost_is_adopted(w):
    w.issues.lose_post = True
    started(w)
    assert w.record()["state"] == "posting"
    w.after_retry()
    w.tick()
    assert w.record()["state"] == "posted"
    assert w.record()["comment_id"] == 101
    agent_done(w)
    w.after_retry()
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
    assert doc["pending"] is False
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


def test_the_chain_end_is_stored_and_delivered_by_the_writer(w):
    started(w)
    w.later(60)
    set_run(w.store, status="failed")
    assert w.finish(w.run()) == {"status": True, "pending": True}
    assert w.issues.edits == []  # the action never calls GitHub
    w.tick()
    assert w.shown().startswith(HANDED_BACK)
    doc = w.record()
    assert doc["final"] is True
    assert doc["outcome"] == "delivered"
    assert doc["pending"] is False


def test_a_chain_without_a_comment_posts_its_final_once(w):
    run = fix_run(status="failed", steps=steps(quiet="succeeded", secrets="failed"))
    w.store.put("runs", run)
    w.finish(run)
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
    assert w.issues.edits == []
    w.later()
    w.tick()
    assert w.shown().startswith("PR fixer stopped: the run was cancelled.")
    assert w.record()["final"] is True


def test_a_chain_idle_after_its_last_run_is_closed(w):
    started(w)
    set_run(w.store, status="succeeded", finished_at="2026-10-09T12:00:01+00:00")
    w.later(60)
    w.tick()
    assert w.record()["final"] is False
    w.later(15 * 60)
    w.tick()
    assert w.shown().startswith("PR fixer: the chain ended.")


# --------------------------------------------------------------------------- failures and horizons


def test_the_backoff_is_capped(w):
    started(w)
    w.finish(w.run())
    w.issues.fail_edit = [GitHubError("http_502", retryable=True)] * 20
    for _ in range(15):
        w.after_retry()
        w.tick()
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
    assert doc["outcome"] == "gave_up"
    assert doc["failures"] == 3
    assert doc["pending"] is False


def _final_failing(w: World) -> None:
    started(w)
    w.finish(w.run())
    w.issues.fail_edit = [GitHubError("http_502", retryable=True)] * 1000


def _edits_failing(w: World) -> None:
    started(w)
    agent_done(w)
    w.issues.fail_edit = [GitHubError("http_502", retryable=True)] * 1000


def _posting_stuck(w: World) -> None:
    w.issues.lose_post = True
    started(w)
    w.issues.list_fails = True


def _never_posted(w: World) -> None:
    w.issues.fail_post = [GitHubError("http_429", retryable=True)] * 1000
    started(w)


@pytest.mark.parametrize(
    ("setup", "horizon"),
    [
        (_final_failing, FINAL_HORIZON),
        (_edits_failing, IDLE_HORIZON),
        (_posting_stuck, IDLE_HORIZON),
        (_never_posted, IDLE_HORIZON),
    ],
    ids=["pending_final", "failing_edits", "posting_unresolved", "never_posted"],
)
def test_every_pending_record_ends_within_its_horizon(w, setup, horizon):
    setup(w)
    w.clock.advance(horizon.total_seconds() + 1)
    w.tick()
    doc = w.record()
    assert doc["pending"] is False
    assert doc["final"] is True
    assert doc["final_at"] is not None  # retention covers it


# --------------------------------------------------------------------------- selection and budget


def test_another_hosts_records_never_starve_this_hosts(w):
    for n in range(200):
        w.store.put(
            STATUS_COLLECTION,
            {
                "id": f"other-{n:03}",
                "pending": True,
                "machine": "spark2",
                "retry_at": "2026-10-01T00:00:00Z",
                "created_at": "2026-10-09T11:00:00Z",
                "rev": 0,
            },
        )
    started(w)
    assert len(w.issues.posts) == 1


def test_a_written_record_moves_to_the_back_of_the_queue(w):
    w.board = w.new_board(max_calls=1)
    w.store.put("runs", fix_run())
    w.store.put("runs", fix_run(id=RUN2, number=8))
    w.tick()
    w.tick()
    assert sorted(p[1] for p in w.issues.posts) == [7, 8]


def test_every_http_request_counts_against_the_budget(w):
    w.board = w.new_board(max_calls=2)
    w.issues.lose_post = True
    started(w)  # the post: one request
    w.issues.pages = 3  # the listing needs three; one is left
    w.after_retry()
    w.tick()
    assert w.issues.listed == 0
    assert w.record()["failures"] == 1  # the waiting listing is no failure
    w.board = w.new_board(max_calls=5)
    w.after_retry()
    w.tick()
    assert w.record()["state"] == "posted"


def test_a_recreate_after_a_404_respects_the_budget(w):
    started(w)
    w.board = w.new_board(max_calls=1)
    w.issues.deleted.add(101)
    agent_done(w)
    w.later()
    w.tick()
    assert len(w.issues.posts) == 1
    assert w.record()["state"] == "none"
    w.later()
    w.tick()
    assert len(w.issues.posts) == 2


def test_every_call_is_bounded_by_the_call_deadline(w):
    started(w)
    agent_done(w)
    w.later(30)
    w.tick()
    assert w.issues.deadlines
    for until in w.issues.deadlines:
        assert (until - w.clock.now).total_seconds() <= CALL_DEADLINE_S


# --------------------------------------------------------------------------- housekeeping


def test_old_final_records_are_dropped(w):
    old = (w.clock.now - RETENTION - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    young = (w.clock.now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"final": True, "pending": False, "created_at": young, "rev": 0}
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
    assert (STATUS_COLLECTION, "status_due") in calls
    assert ("runs", "runs_key_created") in calls
    assert ("bridge_invocations", "bridge_by_run") in calls


def test_a_known_secret_never_reaches_github(w):
    secret = "synthetic-" + "node-secret-0042"
    w.board = StatusBoard(w.store, clock=w.clock, known=lambda: {secret})
    run = fix_run(status="failed")
    w.store.put("runs", run)
    w.finish(run, f"handed back: {secret} and {GHP}")
    w.tick()
    ((_, _, body),) = w.issues.posts
    assert secret not in body
    assert GHP not in body
    assert body.startswith("[withheld]")
