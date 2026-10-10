"""d31: a PR that turns CONFLICTING starts a fixer request, like checks settling does.

:class:`culture_rules.node.conflict_watch.ConflictWatcher` sweeps the open PRs of the fixer
repos and emits one ``github.pr.conflicting`` event per head and base pair."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from culture_rules.apps.github import GitHubError
from culture_rules.events.emit import PR_CONFLICTING_TYPE, reserved_reason
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.node.conflict_watch import (
    DEFAULT_INTERVAL_S,
    REQUESTS_PER_TICK,
    TICK_BUDGET_S,
    ConflictWatcher,
    conflict_event_id,
)
from culture_rules.store.memory import MemoryStore

REPO = "acme/widgets"
HEAD, BASE = "a" * 40, "b" * 40


def pull(number=3, mergeable=False, state="dirty", head=HEAD, base=BASE, **over):
    doc = {
        "number": number,
        "state": "open",
        "draft": False,
        "mergeable": mergeable,
        "mergeable_state": state,
        "user": {"login": "someone"},
        "head": {"sha": head, "ref": "fix", "repo": {"full_name": REPO}},
        "base": {"sha": base, "ref": "main", "repo": {"full_name": REPO}},
    }
    doc.update(over)
    return doc


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 10, tzinfo=UTC)

    def __call__(self):
        return self.now


class FakeGitHub:
    def __init__(self, pulls):
        self.pulls = {p["number"]: p for p in pulls}
        self.reads = []
        self.pages = []
        self.fail_list = False
        self.fail_read = None

    def list_page(self, repo, page, timeout):
        assert 0 < timeout <= 5
        self.pages.append((repo, page))
        if self.fail_list:
            raise GitHubError("http_502", retryable=True)
        listing = list(self.pulls.values())  # no mergeable, but draft and repos
        return listing[(page - 1) * 100 : page * 100]

    def get_pull(self, repo, number, timeout):
        assert 0 < timeout <= 5
        self.reads.append((repo, number))
        if self.fail_read:
            raise GitHubError(self.fail_read, retryable=True)
        return self.pulls[number]


def world(pulls, repos=(REPO,), serves=None, monotonic=None, **variables):
    store = MemoryStore()
    store.put_variable("fixer_repos", list(repos), updated_by="test")
    for name, value in variables.items():
        store.put_variable(name, value, updated_by="test")
    gh, clock = FakeGitHub(pulls), Clock()
    extra = {"monotonic": monotonic} if monotonic else {}
    watcher = ConflictWatcher(store, gh.list_page, gh.get_pull, serves=serves, clock=clock, **extra)
    return store, gh, clock, watcher


def events(store):
    return [d["envelope"] for d in store.find(EVENTS_COLLECTION)]


def test_a_conflicting_pr_emits_one_event_with_its_pr_facts():
    store, _gh, _clock, watcher = world([pull()])
    assert watcher.tick() == 1
    (env,) = events(store)
    assert env["type"] == PR_CONFLICTING_TYPE
    assert env["id"] == conflict_event_id(REPO, 3, HEAD, BASE)
    data = env["data"]
    assert data["repository"] == REPO
    assert data["number"] == 3
    assert data["head_sha"] == HEAD
    assert data["base_sha"] == BASE
    assert data["head_branch"] == "fix"
    assert data["head_repo"] == data["base_repo"] == REPO
    assert data["draft"] is False
    assert data["state"] == "open"
    assert data["mergeable_state"] == "dirty"


def test_the_same_conflict_is_requested_once():
    store, _gh, clock, watcher = world([pull()])
    watcher.tick()
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S + 1)
    assert watcher.tick() == 0
    assert len(events(store)) == 1


def test_a_new_base_is_a_new_request():
    store, gh, clock, watcher = world([pull()])
    watcher.tick()
    gh.pulls[3] = pull(base="c" * 40)
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S + 1)
    assert watcher.tick() == 1
    assert len(events(store)) == 2


def test_mergeable_or_unknown_prs_emit_nothing():
    pulls = [
        pull(1, mergeable=True, state="clean"),
        pull(2, mergeable=None, state="unknown"),  # GitHub still computing
        pull(4, mergeable=False, state="blocked"),
    ]
    store, _gh, _clock, watcher = world(pulls)
    assert watcher.tick() == 0
    assert events(store) == []


def test_a_pr_with_incomplete_facts_is_skipped():
    store, _gh, _clock, watcher = world([pull(user=None)])
    assert watcher.tick() == 0
    assert events(store) == []


def test_the_sweep_waits_for_its_interval():
    store, gh, clock, watcher = world([pull()], conflict_watch_interval_s=60)
    watcher.tick()
    gh.reads.clear()
    clock.now += timedelta(seconds=30)
    assert watcher.tick() == 0
    assert gh.reads == []
    clock.now += timedelta(seconds=31)
    watcher.tick()
    assert gh.reads == [(REPO, 3)]


def test_excluded_and_unserved_repos_are_not_swept():
    _store, gh, _clock, watcher = world([pull()], fixer_excluded_repos=[REPO])
    watcher.tick()
    assert gh.reads == []
    _store, gh, _clock, watcher = world([pull()], serves=lambda repo: False)
    watcher.tick()
    assert gh.reads == []


def test_a_failed_listing_skips_the_repo_until_the_next_sweep():
    store, gh, clock, watcher = world([pull()])
    gh.fail_list = True
    assert watcher.tick() == 0
    gh.fail_list = False
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S + 1)
    assert watcher.tick() == 1


def test_the_conflict_namespace_is_reserved_at_external_ingest():
    forged = {"id": "x1", "type": PR_CONFLICTING_TYPE, "source": "outside", "data": {}}
    assert reserved_reason(forged) is not None
    squat = {"id": conflict_event_id(REPO, 3, HEAD, BASE), "type": "x", "source": "outside"}
    assert reserved_reason(squat) is not None


# --------------------------------------------------------------------------- Codex #3-#5


def test_a_draft_conflict_is_requested_once_it_is_ready():
    store, gh, clock, watcher = world([pull(draft=True)])
    assert watcher.tick() == 0
    assert gh.reads == []  # a draft is never read, so its event is never consumed
    gh.pulls[3] = pull()
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S + 1)
    assert watcher.tick() == 1
    assert events(store)[0]["data"]["draft"] is False


def test_a_fork_pr_is_never_read():
    fork = pull()
    fork["head"]["repo"] = {"full_name": "fork/widgets"}
    store, gh, _clock, watcher = world([fork])
    assert watcher.tick() == 0
    assert gh.reads == []


def test_one_cycle_makes_a_bounded_number_of_requests_and_the_rest_carry_over():
    many = [pull(n) for n in range(1, REQUESTS_PER_TICK + 6)]
    store, gh, _clock, watcher = world(many)
    # the listing page is one request, so one fewer read fits in the first cycle
    assert watcher.tick() == REQUESTS_PER_TICK - 1
    assert len(gh.reads) == REQUESTS_PER_TICK - 1
    assert watcher.tick() == 6  # the next cycle drains the rest, with no new listing
    assert gh.pages == [(REPO, 1)]
    assert len(events(store)) == REQUESTS_PER_TICK + 5


def test_listing_pages_carry_over_too():
    many = [pull(n, mergeable=True, state="clean") for n in range(1, 251)]
    _store, gh, _clock, watcher = world(many)
    for _ in range(40):
        watcher.tick()
    assert gh.pages == [(REPO, 1), (REPO, 2), (REPO, 3)]
    assert len(gh.reads) == 250  # every open PR is read, over many cycles


def test_one_cycle_stops_at_its_time_budget():
    ticks = iter([0.0, 0.0, 0.0, TICK_BUDGET_S + 1] + [TICK_BUDGET_S + 1] * 10)
    store, gh, _clock, watcher = world([pull(1), pull(2)], monotonic=lambda: next(ticks))
    assert watcher.tick() == 1
    assert gh.reads == [(REPO, 1)]


def test_a_rate_limited_read_drops_the_repos_queue_until_the_next_sweep():
    store, gh, clock, watcher = world([pull(1), pull(2), pull(4)])
    gh.fail_read = "http_403"
    assert watcher.tick() == 0
    assert gh.reads == [(REPO, 1)]  # the other two were dropped, not read
    gh.fail_read = None
    assert watcher.tick() == 0  # nothing queued until the interval comes round
    clock.now += timedelta(seconds=DEFAULT_INTERVAL_S + 1)
    assert watcher.tick() == 3
