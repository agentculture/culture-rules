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
        "head": {"sha": "b" * 40, "ref": "feat", "repo": {"full_name": "acme/widgets"}},
        "base": {"sha": "c" * 40, "repo": {"full_name": "acme/widgets"}, "ref": "main"},
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


def test_settled_event_carries_base_sha_and_the_full_pr_fact_set():
    pr = {
        "head": {"sha": "b" * 40, "ref": "feat-now", "repo": {"full_name": "acme/widgets"}},
        "base": {"sha": "c" * 40, "ref": "main", "repo": {"full_name": "acme/widgets"}},
        "draft": False,
        "user": {"login": "alice"},
    }
    store, _, _, settler = make(("a", "completed"), pull=lambda r, n: pr)
    settler.on_check(check_data())
    data = settled(store)[0]["envelope"]["data"]
    assert data["base_sha"] == "c" * 40
    assert data["base_branch"] == "main"
    assert data["pr_author"] == "alice"
    # the settled SHA and the check's branch are the event's own, not the PR's current head
    assert data["head_sha"] == SHA
    assert data["head_branch"] == "feat"


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


def test_app_lister_get_pull_is_read_only_allowlisted_and_bounded():
    import json

    import pytest

    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from culture_rules.node.checks_settle import AppSuiteLister

    pem = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append((method, url, timeout))
        if url.endswith("/access_tokens"):
            expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": "t", "expires_at": expires}).encode()
        return 200, json.dumps({"number": 7, "base": {"sha": "c" * 40}}).encode()

    store = MemoryStore()
    store.insert(
        "actors",
        {
            "id": "gh",
            "kind": "app",
            "params": {
                "surface": "github",
                "connection": {
                    "app_id": "1",
                    "installation_id": "2",
                    "private_key": "grant:K",
                    "repos": [REPO],
                },
            },
        },
    )
    lister = AppSuiteLister(store, transport=transport, secrets=lambda ref: pem)
    assert lister.get_pull(REPO, 7, timeout_s=3)["base"]["sha"] == "c" * 40
    token, read = calls
    assert token[0] == "POST" and token[1].endswith("/access_tokens") and token[2] <= 3
    assert read[0] == "GET" and read[1].endswith(f"/repos/{REPO}/pulls/7") and read[2] <= 3
    with pytest.raises(GitHubError) as err:
        lister.get_pull("other/repo", 7, timeout_s=3)
    assert err.value.code == "repo_not_allowed" and len(calls) == 2


def test_malformed_pull_result_adds_no_pr_facts():
    """A ``{}`` or partial PR must not yield null repos (null == null) or a default draft."""
    partial = {
        "head": {"sha": "b" * 40, "ref": "feat", "repo": None},
        "base": {"sha": "c" * 40, "ref": "main", "repo": {"full_name": "acme/widgets"}},
        "draft": False,
        "user": {"login": "alice"},
    }
    for pr in ({}, partial, {"draft": "false"}):
        store, _, _, settler = make(("a", "completed"), pull=lambda r, n, pr=pr: pr)
        assert settler.on_check(check_data()) == "emitted"
        data = settled(store)[0]["envelope"]["data"]
        for key in ("head_repo", "base_repo", "base_branch", "base_sha", "draft", "pr_author"):
            assert key not in data, (pr, key)
        assert data["head_sha"] == SHA and data["head_branch"] == "feat"


def _pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def _lister_store():
    store = MemoryStore()
    store.insert(
        "actors",
        {
            "id": "gh",
            "kind": "app",
            "params": {
                "surface": "github",
                "connection": {
                    "app_id": "1",
                    "installation_id": "2",
                    "private_key": "grant:K",
                    "repos": [REPO],
                },
            },
        },
    )
    return store


def _ok_transport(method, url, headers, body, timeout):
    import json

    if url.endswith("/access_tokens"):
        expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        return 201, json.dumps({"token": "t", "expires_at": expires}).encode()
    return 200, json.dumps({"number": 7, "base": {"sha": "c" * 40}}).encode()


class SlowSecrets:
    """A ``grant get`` stand-in that blocks until released (a cold, slow secret resolve)."""

    def __init__(self, pem):
        import threading

        self.pem = pem
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, ref):
        self.calls += 1
        self.release.wait(10)
        return self.pem


def test_app_lister_bound_covers_cold_secret_resolution_and_warms_the_cache():
    import time

    import pytest

    pytest.importorskip("cryptography")
    from culture_rules.node.checks_settle import AppSuiteLister

    secrets = SlowSecrets(_pem())
    lister = AppSuiteLister(_lister_store(), transport=_ok_transport, secrets=secrets)
    started = time.monotonic()
    with pytest.raises(GitHubError) as err:
        lister.get_pull(REPO, 7, timeout_s=0.2)
    assert err.value.code == "deadline_exceeded" and err.value.retryable
    assert time.monotonic() - started < 2  # not held for the secret resolve
    secrets.release.set()  # the resolve finishes in the background and caches the App
    deadline = time.monotonic() + 5
    while "gh" not in lister._apps:  # the timed-out worker warmed the per-actor App cache
        assert time.monotonic() < deadline
        time.sleep(0.01)
    while True:  # its slot frees once its (deadline-cut) token call has failed
        try:
            pr = lister.get_pull(REPO, 7, timeout_s=1)
            break
        except GitHubError as exc:
            assert exc.code == "lookup_busy" and time.monotonic() < deadline
            time.sleep(0.01)
    assert pr["base"]["sha"] == "c" * 40
    assert secrets.calls == 1  # steady state reuses the warmed App, no second resolve


def test_app_lister_caps_the_threads_stuck_on_slow_lookups():
    import threading
    import time

    import pytest

    pytest.importorskip("cryptography")
    from culture_rules.node.checks_settle import LOOKUP_WORKERS, AppSuiteLister

    secrets = SlowSecrets(_pem())
    lister = AppSuiteLister(_lister_store(), transport=_ok_transport, secrets=secrets)
    before = threading.active_count()
    codes = []
    try:
        for _ in range(LOOKUP_WORKERS + 5):
            started = time.monotonic()
            with pytest.raises(GitHubError) as err:
                lister.get_pull(REPO, 7, timeout_s=0.05)
            assert time.monotonic() - started < 1
            codes.append(err.value.code)
        assert threading.active_count() - before <= LOOKUP_WORKERS
        assert secrets.calls <= LOOKUP_WORKERS
        assert codes.count("lookup_busy") >= 5
    finally:
        secrets.release.set()
