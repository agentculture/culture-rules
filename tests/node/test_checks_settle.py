"""Once-per-SHA checks settle: criteria 1-3 of t12, plus dedupe, restart and the hook seam."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from culture_rules.apps.github import GitHubError
from culture_rules.events.ingest import EVENTS_COLLECTION
from culture_rules.node.checks_settle import (
    SETTLE_COLLECTION,
    SETTLED_TYPE,
    ChecksSettler,
    settled_event_id,
)
from culture_rules.store.memory import MemoryStore

REPO, SHA = "acme/widgets", "a" * 40
T0 = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Suites:
    def __init__(self, *suites):
        self.suites = {slug: status for slug, status in suites}
        self.calls = 0
        self.fail = False

    def __call__(self, repo, sha):
        self.calls += 1
        if self.fail:
            raise GitHubError("network_error", retryable=True)
        return [{"app_slug": s, "status": st, "conclusion": None} for s, st in self.suites.items()]


def check_data(**extra):
    data = {"repository": REPO, "head_sha": SHA, "head_branch": "feat", "pr_numbers": [7]}
    data.update(extra)
    return data


def settled(store):
    return [d for d in store.find(EVENTS_COLLECTION) if d["envelope"]["type"] == SETTLED_TYPE]


def make(*suites, clock=None, **kw):
    store = MemoryStore()
    lister = Suites(*suites)
    clock = clock or Clock()
    return store, lister, clock, ChecksSettler(store, lister, clock=clock, **kw)


def test_exactly_one_event_after_last_of_three_suites_completes():
    store, lister, _, settler = make(
        ("github-actions", "in_progress"),
        ("sonarqubecloud", "in_progress"),
        ("gitguardian", "in_progress"),
    )
    for slug in ("github-actions", "sonarqubecloud"):
        lister.suites[slug] = "completed"
        assert settler.on_check(check_data()) == "pending"
        assert settled(store) == []
    lister.suites["gitguardian"] = "completed"
    assert settler.on_check(check_data()) == "emitted"
    # redelivery / a late event / another node: still one
    assert settler.on_check(check_data()) == "duplicate"
    assert settler.tick() == 0
    [event] = settled(store)
    data = event["envelope"]["data"]
    assert data["settled_by"] == "all_completed"
    assert data["head_sha"] == SHA and data["repository"] == REPO and data["number"] == 7


def test_queued_suite_of_ignored_app_does_not_hold_back_event():
    store, _, _, settler = make(("github-actions", "completed"), ("claude", "queued"))
    assert settler.on_check(check_data()) == "emitted"
    assert len(settled(store)) == 1


def test_ignored_apps_variable_overrides_default():
    store, lister, _, settler = make(("github-actions", "completed"), ("claude", "queued"))
    store.put_variable("ignored_check_apps", ["other"], updated_by="t")
    assert settler.on_check(check_data()) == "pending"  # claude no longer ignored
    store.put_variable("ignored_check_apps", ["Claude"], updated_by="t")  # case-insensitive
    assert settler.on_check(check_data()) == "emitted"


def test_timeout_emits_one_event_settled_by_timeout():
    store, _, clock, settler = make(
        ("github-actions", "completed"), ("sonarqubecloud", "in_progress")
    )
    store.put_variable("checks_settle_timeout_s", 60, updated_by="t")
    assert settler.on_check(check_data()) == "pending"
    clock.now = T0 + timedelta(seconds=59)
    assert settler.tick() == 0
    clock.now = T0 + timedelta(seconds=61)
    assert settler.tick() == 1
    assert settler.tick() == 0
    [event] = settled(store)
    assert event["envelope"]["data"]["settled_by"] == "timeout"
    # a completion arriving after the timeout does not emit a second event
    assert settler.on_check(check_data()) == "duplicate"


def test_timeout_survives_a_restart_and_a_second_settler_dedupes():
    store, lister, clock, settler = make(("a", "in_progress"))
    settler.on_check(check_data())
    fresh = ChecksSettler(store, lister, clock=clock)  # new process, same store
    other = ChecksSettler(store, lister, clock=clock)
    clock.now = T0 + timedelta(hours=1)
    assert fresh.tick() + other.tick() == 1
    assert len(settled(store)) == 1
    assert store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}")["state"] == "emitted"


def test_first_deadline_stands_across_more_completions():
    store, _, clock, settler = make(("a", "in_progress"))
    settler.on_check(check_data())
    clock.now = T0 + timedelta(seconds=800)
    settler.on_check(check_data())
    clock.now = T0 + timedelta(seconds=901)  # default 900 s from the FIRST completion
    assert settler.tick() == 1


def test_variable_defaults_and_bad_values():
    store, _, _, settler = make()
    assert settler.ignored_apps() == {"claude"}
    assert settler.timeout_s() == 900.0
    store.put_variable("ignored_check_apps", "claude", updated_by="t")
    store.put_variable("checks_settle_timeout_s", -5, updated_by="t")
    assert settler.ignored_apps() == {"claude"}
    assert settler.timeout_s() == 900.0


def test_listing_failure_is_retried_not_lost():
    store, lister, _, settler = make(("a", "completed"))
    lister.fail = True
    assert settler.on_check(check_data()) == "error"
    lister.fail = False
    assert settler.on_check(check_data()) == "emitted"


def test_event_without_sha_is_ignored_and_shas_are_independent():
    store, _, _, settler = make(("a", "completed"))
    assert settler.on_check({"repository": REPO}) == "ignored"
    assert settler.on_check(check_data()) == "emitted"
    assert settler.on_check(check_data(head_sha="b" * 40)) == "emitted"
    assert settled_event_id(REPO, SHA) != settled_event_id(REPO, "b" * 40)
    assert len(settled(store)) == 2


def test_pull_enrichment_is_best_effort():
    pr = {
        "head": {"repo": {"full_name": "acme/widgets"}},
        "base": {"repo": {"full_name": "acme/widgets"}, "ref": "main"},
        "draft": False,
        "user": {"login": "alice"},
    }
    store, _, _, settler = make(("a", "completed"), pull=lambda r, n: pr)
    settler.on_check(check_data())
    data = settled(store)[0]["envelope"]["data"]
    assert data["head_repo"] == data["base_repo"] == "acme/widgets" and data["draft"] is False

    def boom(r, n):
        raise GitHubError("http_404")

    store, _, _, settler = make(("a", "completed"), pull=boom)
    assert settler.on_check(check_data()) == "emitted"
    assert "head_repo" not in settled(store)[0]["envelope"]["data"]
