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
    store.put_variable("checks_settle_min_s", 0, updated_by="t")
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
    assert event["envelope"]["data"]["conclusion"] == "timeout"
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


def test_transient_failure_on_only_completion_is_retried_by_tick():
    store, lister, clock, settler = make(("a", "completed"))
    lister.fail = True
    assert settler.on_check(check_data()) == "error"
    assert settler.tick() == 0  # still failing
    lister.fail = False
    assert settler.tick() == 0  # the failed poll backs off
    clock.now = T0 + timedelta(seconds=16)
    assert settler.tick() == 1
    assert [e["envelope"]["data"]["settled_by"] for e in settled(store)] == ["all_completed"]


def test_persistent_failure_still_times_out_once():
    store, lister, clock, settler = make(("a", "completed"))
    lister.fail = True
    settler.on_check(check_data())
    clock.now = T0 + timedelta(seconds=901)
    assert settler.tick() == 1
    assert settler.tick() == 0
    assert settled(store)[0]["envelope"]["data"]["settled_by"] == "timeout"


def test_later_completion_merges_pr_numbers_but_keeps_first_deadline():
    store, _, clock, settler = make(("a", "in_progress"))
    settler.on_check({"repository": REPO, "head_sha": SHA})  # no PR facts yet
    clock.now = T0 + timedelta(seconds=100)
    settler.on_check(check_data(head_branch="feat"))
    rec = store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}")
    assert rec["pr_numbers"] == [7] and rec["head_branch"] == "feat"
    clock.now = T0 + timedelta(seconds=901)
    assert settler.tick() == 1
    data = settled(store)[0]["envelope"]["data"]
    assert data["number"] == 7 and data["settled_by"] == "timeout"


def test_min_window_holds_back_event_for_a_late_appearing_suite():
    store, lister, clock, settler = make(("github-actions", "completed"))
    store.put_variable("checks_settle_min_s", 60, updated_by="t")
    assert settler.on_check(check_data()) == "pending"  # all listed complete, window open
    lister.suites["sonarqubecloud"] = "in_progress"  # appears within the window
    clock.now = T0 + timedelta(seconds=61)
    assert settler.tick() == 0 and settled(store) == []
    lister.suites["sonarqubecloud"] = "completed"
    clock.now = T0 + timedelta(seconds=80)  # past the poll backoff
    assert settler.tick() == 1
    assert settled(store)[0]["envelope"]["data"]["settled_by"] == "all_completed"


def test_min_window_default_is_60s():
    assert ChecksSettler(MemoryStore(), Suites()).min_s() == 60.0


def test_polls_are_claimed_once_per_interval_across_nodes_and_deadline_still_emits():
    store, lister, clock, a = make(("x", "in_progress"))
    b = ChecksSettler(store, lister, clock=clock)
    a.on_check(check_data())
    base = lister.calls  # the webhook's own listing
    for _ in range(20):  # many cycles on two nodes within one interval
        a.tick()
        b.tick()
    assert lister.calls == base + 1
    clock.now = T0 + timedelta(seconds=16)
    a.tick()
    b.tick()
    assert lister.calls == base + 2
    clock.now = T0 + timedelta(seconds=40)  # second interval backed off to 30 s: not due
    a.tick()
    assert lister.calls == base + 2
    clock.now = T0 + timedelta(seconds=47)
    a.tick()
    assert lister.calls == base + 3
    clock.now = T0 + timedelta(seconds=901)
    assert a.tick() + b.tick() == 1
    assert len(settled(store)) == 1
    assert settled(store)[0]["envelope"]["data"]["settled_by"] == "timeout"


def test_next_poll_never_runs_past_the_deadline():
    store, lister, clock, settler = make(("x", "in_progress"))
    store.put_variable("checks_settle_timeout_s", 20, updated_by="t")
    settler.on_check(check_data())
    clock.now = T0 + timedelta(seconds=16)
    settler.tick()
    rec = store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}")
    assert rec["next_poll_at"] == rec["deadline"]


def test_settle_conclusions_include_only_counted_suites():
    for conclusions, expected in [
        (["success", "neutral", "skipped"], "success"),
        (["failure"], "failure"),
        ([None], "failure"),
    ]:
        store, _, clock, _ = make()
        suites = [{"app_slug": "ci", "status": "completed", "conclusion": c} for c in conclusions]
        suites.append({"app_slug": "claude", "status": "completed", "conclusion": "failure"})
        settler = ChecksSettler(store, lambda *_: suites, clock=clock)
        assert settler.on_check(check_data()) == "emitted"
        assert settled(store)[0]["envelope"]["data"]["conclusion"] == expected
