"""d34 (#40): ``queue.stop`` ends a PR's fixer story; ``queue.add`` and the push honour it.

A trusted ``/stop`` (or a 👎) runs the ``queue-stop`` workflow: its ``queue.stop`` built-in
records the stop on the PR's concurrency key, removes the PR's queued request and retries,
revokes a dispatch no run claimed yet and cancels the PR's running fixer runs whose story
began before the stop (no push, no hand-back). From then on ``queue.add`` quietly drops a
request of the stopped story, or an automatic request for the head the story was stopped
at; a new ``/fix`` (a rule that resets the attempt budget) or a new head starts a new story,
and the push refuses ``story_stopped`` for any run of the stopped story that slipped through.
"""

from __future__ import annotations

from datetime import timedelta

from culture_rules.engine.actorport import COMPLETED, FAILED
from culture_rules.engine.runs import RUNS_COLLECTION
from culture_rules.node.actions.queue import QUEUES_COLLECTION, QueueStopPort
from culture_rules.node.story_stop import STOPS_COLLECTION, chain_stopped, stop_of
from tests.node.test_queue import CONFIG, DEADLINE, World, ctx, request

KEY = "pr-fixer:o/a#1"
STOP = {**CONFIG}


def iso(clock, delta=0):
    return (clock.now + timedelta(seconds=delta)).isoformat()


def run(
    w,
    rid,
    *,
    key=KEY,
    status="running",
    created=-60,
    kind="github.comment.created",
    resets=False,
    number=1,
    head="a" * 40,
):
    doc = {
        "id": rid,
        "status": status,
        "rule_id": "pr-fixer-comment",
        "workflow_id": "pr-fix",
        "concurrency_key": key,
        "created_at": iso(w.clock, created),
        "rev": 1,
        "steps": [{"key": "fix", "def": "fix", "status": "running"}],
        "history": [],
        "trigger": {
            "type": kind,
            "data": {"repository": "o/a", "number": number, "head_sha": head},
        },
        "rule": {"definition": {"id": "pr-fixer-comment", "resets_attempt_budget": resets}},
    }
    w.store.put(RUNS_COLLECTION, doc)
    return doc


class StopWorld(World):
    def __init__(self, lookup=None):
        super().__init__()
        self.stop_port = QueueStopPort(self.store, clock=self.clock, pr_lookup=lookup)

    def stop(self, config=STOP, run_id="stop-1", **inputs):
        self.n += 1
        given = {"repo": "o/a", "number": 1, "by": "OriNachum", "head_sha": "a" * 40, **inputs}
        return self.stop_port.invoke(
            given, f"ik-stop-{self.n}", DEADLINE, context=ctx(config, step="stop", run=run_id)
        )

    def add_as(self, run_id, **req):
        self.n += 1
        return self.add_port.invoke(
            req, f"ik-add-{self.n}", DEADLINE, context=ctx(CONFIG, run=run_id)
        )


def test_stop_removes_the_prs_queued_request_and_keeps_the_others():
    w = StopWorld()
    w.add(**request("o/a", 1))
    w.add(**request("o/b", 2))
    res = w.stop()
    assert res.outcome == COMPLETED
    assert res.output["stopped"] is True
    assert res.output["removed"] == 1
    assert res.output["key"] == KEY
    assert w.waiting() == [("o/b#2", 1)]
    stop = stop_of(w.store, KEY)
    assert stop["by"] == "OriNachum"
    assert stop["head_sha"] == "a" * 40
    assert stop["at"] == w.clock.now.isoformat()


def test_stop_removes_a_queued_retry_too():
    w = StopWorld()
    w.add(**request("o/a", 1, retry=True, task="fix it"))
    assert w.stop().output["removed"] == 1
    assert w.doc()["waiting"] == []


def test_stop_revokes_an_unclaimed_dispatch_and_keeps_a_claimed_one():
    w = StopWorld()
    w.add(**request("o/a", 1))
    w.progress()
    assert w.active() == ["o/a#1"]
    w.stop()
    assert w.active() == []  # its dispatch event can no longer fire (dispatch_revoked)
    w.add(**request("o/b", 2))
    w.progress()
    doc = w.doc()
    doc["active"][0]["claimed_at"] = w.clock.now.isoformat()
    w.store.put(QUEUES_COLLECTION, doc)
    w.stop(repo="o/b", number=2)
    assert w.active() == ["o/b#2"]  # claimed: its run is (being) started; it is cancelled


def test_stop_cancels_the_running_runs_of_the_story_and_nothing_else():
    w = StopWorld()
    run(w, "fix-1")
    run(w, "other", key="pr-fixer:o/b#2", number=2)
    run(w, "done", status="succeeded")
    res = w.stop()
    assert res.output["cancelled"] == 1
    fix = w.store.get(RUNS_COLLECTION, "fix-1")
    assert fix["status"] == "cancelled"
    assert fix["error"]["code"] == "cancelled"
    assert "OriNachum" in fix["error"]["message"]
    assert all(s["status"] == "cancelled" for s in fix["steps"])
    assert w.store.get(RUNS_COLLECTION, "other")["status"] == "running"
    assert w.store.get(RUNS_COLLECTION, "done")["status"] == "succeeded"


def test_a_sweep_cancels_a_late_run_of_the_stopped_story_but_never_a_new_story():
    w = StopWorld()
    w.stop()
    w.clock.advance(120)
    run(w, "late", created=-130)  # its story began before the stop: it slipped through
    run(w, "new-story", created=-10)  # a /fix after the stop
    res = w.stop(config={**STOP, "sweep": True})
    assert res.output["cancelled"] == 1
    assert w.store.get(RUNS_COLLECTION, "late")["status"] == "cancelled"
    assert w.store.get(RUNS_COLLECTION, "new-story")["status"] == "running"
    assert stop_of(w.store, KEY)["at"] == iso(w.clock, -120)  # a sweep records nothing


def test_a_sweep_without_a_stop_does_nothing():
    w = StopWorld()
    run(w, "fix-1")
    res = w.stop(config={**STOP, "sweep": True})
    assert res.output == {"stopped": False, "key": KEY, "removed": 0, "cancelled": 0}
    assert w.store.get(RUNS_COLLECTION, "fix-1")["status"] == "running"


def test_a_request_of_the_stopped_story_is_not_queued_and_does_not_fail():
    w = StopWorld()
    run(w, "retry-add", status="running", created=-30)  # a retry's queue.add, story before
    w.stop()
    res = w.add_as("retry-add", **request("o/a", 1, head="c" * 40, retry=True))
    assert res.outcome == COMPLETED
    assert res.output["queued"] is False
    assert w.doc()["waiting"] == []


def test_an_automatic_request_for_the_stopped_head_is_not_queued():
    w = StopWorld()
    w.stop()
    w.clock.advance(5)
    run(w, "settle-add", created=0, kind="github.pr.checks_settled")
    res = w.add_as("settle-add", **request("o/a", 1, head="a" * 40))
    assert res.output["queued"] is False
    run(w, "push-add", created=0, kind="github.pr.checks_settled", head="d" * 40)
    res = w.add_as("push-add", **request("o/a", 1, head="d" * 40))
    assert res.output["queued"] is True  # a new head: a new story


def test_a_fix_after_the_stop_starts_a_new_story_on_the_same_head():
    w = StopWorld()
    w.stop()
    w.clock.advance(5)
    run(w, "fix-add", created=0, resets=True)  # pr-fixer-comment resets the budget (d32)
    res = w.add_as("fix-add", **request("o/a", 1, head="a" * 40))
    assert res.output["queued"] is True
    assert w.waiting() == [("o/a#1", 1)]


def test_another_pr_is_never_affected_by_a_stop():
    w = StopWorld()
    w.stop()
    assert w.add(**request("o/b", 2)).output["queued"] is True


def test_the_push_refuses_a_chain_whose_story_began_before_the_stop():
    w = StopWorld()
    root = run(w, "root", created=-60)
    fix = run(w, "fix", created=-30)
    assert chain_stopped(w.store, [fix, root]) is None
    w.stop()
    assert chain_stopped(w.store, [fix, root]) == "story_stopped"
    w.clock.advance(10)
    newer = run(w, "newer", created=0)
    assert chain_stopped(w.store, [newer]) is None
    assert chain_stopped(w.store, []) is None
    assert chain_stopped(w.store, [run(w, "unkeyed", key=None)]) is None


def test_stop_reads_the_prs_current_head_when_it_can():
    from tests.engine.run_helpers import FakeActor

    head = FakeActor(default=lambda inp, ctx: {"head_sha": "e" * 40, "base_sha": "b" * 40})
    w = StopWorld(lookup=head)
    w.stop(config={**STOP, "lookup_actor": "github-app"})
    assert stop_of(w.store, KEY)["head_sha"] == "e" * 40


def test_stop_refuses_a_malformed_request():
    w = StopWorld()
    assert w.stop(number=0).outcome == FAILED
    assert w.stop(by="").outcome == FAILED
    res = w.stop(config={"queue": "pr-fixer"})  # no key prefix: no key to stop
    assert res.outcome == FAILED
    assert w.store.find(STOPS_COLLECTION) == []


# --------------------------------------------------------------------------- Codex round 1


def test_a_retry_of_a_fix_begun_after_the_stop_is_queued_on_the_stopped_head():
    """A new /fix on the stopped head is a new story: its retries belong to it (Codex #4)."""
    w = StopWorld()
    w.stop()
    w.clock.advance(5)
    root = run(w, "fix-add", created=0, resets=True)
    retry = run(w, "retry-add", created=1, kind="rules.run.succeeded")
    w.store.put(RUNS_COLLECTION, {**retry, "story_root_for_test": root["id"]})
    from culture_rules.node import story_stop

    original = story_stop._root
    story_stop._root = lambda store, r: root if r["id"] == "retry-add" else original(store, r)
    try:
        res = w.add_as("retry-add", **request("o/a", 1, head="a" * 40, retry=True))
    finally:
        story_stop._root = original
    assert res.output["queued"] is True


def test_a_stopped_storys_retry_is_dropped_before_the_budget_is_judged():
    """A stopped story's last retry never fails as attempt_budget_exhausted (Codex #6)."""
    from culture_rules.engine.claims import RULE_ATTEMPT_BUDGETS, budget_id

    w = StopWorld()
    run(w, "retry-add", created=-30)
    w.store.put(RULE_ATTEMPT_BUDGETS, {"id": budget_id(KEY), "key": KEY, "count": 3, "limit": 3})
    w.stop()
    res = w.add_as("retry-add", **request("o/a", 1, retry=True))
    assert res.outcome == COMPLETED
    assert res.output["queued"] is False


def test_an_add_racing_a_stop_never_leaves_a_request_behind():
    """The stop always moves the queue document, and queue.add judges the stop inside its
    compare-and-set: whichever lands second sees the other (Codex #3)."""
    w = StopWorld()
    run(w, "story-add", created=-30)
    real_get = w.store.get
    fired = {"done": False}

    def get(collection, doc_id):
        # the add read the stop (none) and the queue; the stop lands before its write
        if collection == QUEUES_COLLECTION and not fired["done"]:
            fired["done"] = True
            out = real_get(collection, doc_id)
            w.stop()
            return out
        return real_get(collection, doc_id)

    w.store.get = get
    res = w.add_as("story-add", **request("o/a", 1, head="c" * 40))
    w.store.get = real_get
    assert res.output["queued"] is False
    assert (w.doc() or {}).get("waiting", []) == []


def test_progress_never_dispatches_a_request_of_a_stopped_story():
    """A request that slipped into the queue is dropped at dispatch (Codex #3)."""
    w = StopWorld()
    run(w, "story-add", created=-30)
    w.add_as("story-add", **request("o/a", 1))
    from culture_rules.node.story_stop import record_stop

    record_stop(w.store, KEY, by="OriNachum", head_sha="a" * 40, at=w.clock.now)
    out = w.progress().output
    assert out["dispatched"] == []
    assert out["dropped"] == [{"key": "o/a#1", "reason": "story_stopped"}]


def test_an_older_stop_never_replaces_a_newer_one():
    """Stops are monotonic: a writer that captured an older time loses (Codex #7)."""
    from culture_rules.node.story_stop import record_stop

    w = StopWorld()
    newer = record_stop(w.store, KEY, by="newer", head_sha="b" * 40, at=w.clock.now)
    older = record_stop(
        w.store, KEY, by="older", head_sha="a" * 40, at=w.clock.now - timedelta(seconds=10)
    )
    assert older == newer
    assert stop_of(w.store, KEY)["by"] == "newer"


def test_a_reaction_on_a_story_that_ended_stops_nothing():
    """A late 👎 names its story; once that story is over it never stops a newer one
    (Codex #5)."""
    w = StopWorld()
    run(w, "old-root", status="succeeded", created=-300)
    newer = run(w, "new-root", created=-10)
    res = w.stop(story="old-root")
    assert res.output["stopped"] is False
    assert stop_of(w.store, KEY) is None
    assert w.store.get(RUNS_COLLECTION, newer["id"])["status"] == "running"
    res = w.stop(story="new-root")
    assert res.output["stopped"] is True
    assert w.store.get(RUNS_COLLECTION, newer["id"])["status"] == "cancelled"
