"""d34 (#40): a 👎 on a story's /fix comment or status comment stops the story.

GitHub sends no webhook for reactions, so
:class:`culture_rules.node.reaction_watch.ReactionWatcher` reads the thumbs-down reactions
on the two comments of every live fixer story (queued, or with a status comment not yet
final) about once a minute and emits one ``github.reaction.added`` event per reaction."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from culture_rules.apps.github import GitHubError
from culture_rules.events.emit import REACTION_ADDED_TYPE, reserved_reason
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.node.actions.queue import QUEUES_COLLECTION
from culture_rules.node.fixer_status import STATUS_COLLECTION
from culture_rules.node.reaction_watch import (
    DEFAULT_INTERVAL_S,
    REQUESTS_PER_TICK,
    TICK_BUDGET_S,
    ReactionWatcher,
    reaction_event_id,
)
from culture_rules.store.memory import MemoryStore

REPO = "acme/widgets"
HEAD = "a" * 40


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, tzinfo=UTC)

    def __call__(self):
        return self.now


class FakeGitHub:
    """Reactions per comment id, in GitHub's shape."""

    def __init__(self):
        self.reactions: dict[int, list[dict]] = {}
        self.reads: list[tuple[str, int]] = []
        self.fail: str | None = None

    def add(self, comment_id, login, content="-1", rid=None):
        items = self.reactions.setdefault(comment_id, [])
        items.append(
            {
                "id": rid or 1000 + len(items) + 10 * comment_id,
                "content": content,
                "user": {"login": login},
                "created_at": "2026-10-10T00:00:00Z",
            }
        )

    def list_reactions(self, repo, comment_id, timeout):
        assert 0 < timeout <= 5
        self.reads.append((repo, comment_id))
        if self.fail:
            raise GitHubError(self.fail, retryable=True)
        return list(self.reactions.get(comment_id, []))


def root_run(store, run_id="run-root", *, kind="github.comment.created", comment_id=11, number=3):
    data = {"repository": REPO, "number": number, "head_sha": HEAD, "author": "OriNachum"}
    if comment_id is not None:
        data["comment_id"] = comment_id
    store.put(
        "runs",
        {
            "id": run_id,
            "status": "running",
            "rule_id": "pr-fixer-comment",
            "created_at": "2026-10-10T00:00:00+00:00",
            "trigger": {"type": kind, "data": data},
        },
    )


def status_record(store, root_id="run-root", *, comment_id=22, final=False, number=3):
    store.put(
        STATUS_COLLECTION,
        {
            "id": root_id,
            "repo": REPO,
            "number": number,
            "state": "posted" if comment_id else "none",
            "comment_id": comment_id,
            "final": final,
            "pending": not final,
        },
    )


def queued(store, source_run="run-root", number=3):
    store.put(
        QUEUES_COLLECTION,
        {
            "id": "pr-fixer",
            "queue": "pr-fixer",
            "waiting": [
                {
                    "rid": "1-x",
                    "key": f"{REPO}#{number}",
                    "repository": REPO,
                    "number": number,
                    "head_sha": HEAD,
                    "source_run": source_run,
                }
            ],
            "active": [],
            "rev": 1,
        },
    )


def world(monotonic=None):
    store = MemoryStore()
    gh, clock = FakeGitHub(), Clock()
    extra = {"monotonic": monotonic} if monotonic else {}
    watcher = ReactionWatcher(store, gh.list_reactions, clock=clock, **extra)
    return store, gh, clock, watcher


def emitted(store):
    return [d["envelope"] for d in store.find(EVENTS_COLLECTION)]


def test_no_live_story_means_no_request():
    store, gh, _clock, watcher = world()
    root_run(store)
    status_record(store, final=True)  # a story that ended
    assert watcher.tick() == 0
    assert gh.reads == []


def test_a_live_storys_fix_and_status_comments_are_read_and_each_thumbs_down_emits_once():
    store, gh, clock, watcher = world()
    root_run(store)
    status_record(store)
    gh.add(11, "OriNachum", rid=501)
    gh.add(22, "mallory", rid=502)  # the rule decides who is trusted, not the watcher
    gh.add(22, "OriNachum", content="+1", rid=503)  # not a thumbs-down
    assert watcher.tick() == 2
    assert sorted(gh.reads) == [(REPO, 11), (REPO, 22)]
    events = {e["data"]["reaction_id"]: e for e in emitted(store)}
    assert set(events) == {501, 502}
    fix = events[501]
    assert fix["type"] == REACTION_ADDED_TYPE
    assert fix["id"] == reaction_event_id(REPO, 11, 501)
    assert fix["data"] == {
        "repository": REPO,
        "number": 3,
        "comment_id": 11,
        "comment": "fix",
        "content": "-1",
        "author": "OriNachum",
        "reaction_id": 501,
        "story": "run-root",
        "head_sha": HEAD,
    }
    assert events[502]["data"]["comment"] == "status"
    # the next sweep reads again but never emits the same reaction twice
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S)
    assert watcher.tick() == 0
    assert len(gh.reads) == 4
    assert len(emitted(store)) == 2


def test_a_queued_story_without_a_status_comment_is_watched_through_its_request():
    store, gh, _clock, watcher = world()
    root_run(store)
    queued(store)
    gh.add(11, "OriNachum")
    assert watcher.tick() == 1
    assert gh.reads == [(REPO, 11)]


def test_a_story_not_started_by_a_comment_watches_its_status_comment_only():
    store, gh, _clock, watcher = world()
    root_run(store, kind="github.pr.checks_settled", comment_id=None)
    status_record(store)
    watcher.tick()
    assert gh.reads == [(REPO, 22)]


def test_a_review_comment_is_not_an_issue_comment_and_is_not_read():
    store, gh, _clock, watcher = world()
    root_run(store, kind="github.review_comment.created", comment_id=33)
    status_record(store)
    watcher.tick()
    assert gh.reads == [(REPO, 22)]


def test_a_sweep_waits_for_the_interval_and_the_variable_tunes_it():
    store, gh, clock, watcher = world()
    root_run(store)
    status_record(store)
    watcher.tick()
    assert len(gh.reads) == 2
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S - 1)
    watcher.tick()
    assert len(gh.reads) == 2
    store.put_variable("reaction_watch_interval_s", 5, updated_by="test")
    watcher.tick()
    assert len(gh.reads) == 4


def test_a_cycle_makes_at_most_its_budget_of_requests_and_carries_the_rest_over():
    store, gh, _clock, watcher = world()
    for n in range(1, REQUESTS_PER_TICK + 4):
        root_run(store, f"run-{n}", comment_id=100 + n, number=n)
        status_record(store, f"run-{n}", comment_id=200 + n, number=n)
    watcher.tick()
    assert len(gh.reads) == REQUESTS_PER_TICK
    watcher.tick()  # not due again: the carried-over reads go first
    assert len(gh.reads) == 2 * REQUESTS_PER_TICK
    watcher.tick()
    assert len(gh.reads) == 2 * (REQUESTS_PER_TICK + 3)
    assert len(set(gh.reads)) == len(gh.reads)


def test_a_cycle_stops_at_its_time_budget():
    ticks = iter([0.0, 0.0, TICK_BUDGET_S + 1] + [TICK_BUDGET_S + 1] * 50)
    store, gh, _clock, watcher = world(monotonic=lambda: next(ticks))
    root_run(store)
    status_record(store)
    watcher.tick()
    assert len(gh.reads) == 1


def test_a_failed_read_skips_that_comment_until_the_next_sweep():
    store, gh, clock, watcher = world()
    root_run(store)
    status_record(store)
    gh.add(11, "OriNachum")
    gh.fail = "http_502"
    assert watcher.tick() == 0
    gh.fail = None
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S)
    assert watcher.tick() == 1


def test_the_reaction_type_and_id_prefix_are_reserved_at_external_ingest():
    forged = {"id": "evt-1", "type": REACTION_ADDED_TYPE, "source": "x", "data": {}}
    assert reserved_reason(forged)
    squat = {"id": reaction_event_id(REPO, 11, 1), "type": "github.other", "source": "x"}
    assert reserved_reason(squat)
