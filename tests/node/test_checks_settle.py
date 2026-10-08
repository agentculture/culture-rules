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


# ---------------------------------------------------------------- recovery (risk r14)


def stored_check(store, clock, id="completion", **data):
    """A check completion as the webhook stores it, received at ``clock()``."""
    from culture_rules.events.ingest import event_document

    store.insert(
        EVENTS_COLLECTION,
        event_document(
            {"id": id, "type": "github.checks.suite_completed", "data": check_data(**data)},
            host="webhook",
            received_at=clock(),
        ),
    )


def past_grace(clock):
    from culture_rules.node.checks_settle import RECOVERY_GRACE_S

    clock.now += timedelta(seconds=RECOVERY_GRACE_S + 1)


def test_recovery_arms_an_unarmed_completion_as_of_receipt_and_fires_once():
    store, lister, clock, settler = make(("a", "completed"))
    store.put_variable("checks_settle_min_s", 60, updated_by="t")
    stored_check(store, clock)  # the webhook stored it; its arm failed (no record)
    received = clock.now
    clock.now += timedelta(seconds=30)
    assert settler.tick() == 0  # inside the grace: an arm may still be in flight
    assert store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}") is None
    past_grace(clock)
    assert settler.tick() == 1
    record = store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}")
    assert record["armed_at"] == received.isoformat()  # as if the webhook had armed it
    [event] = settled(store)
    assert event["envelope"]["data"]["settled_by"] == "all_completed"
    assert event["envelope"]["data"]["number"] == 7
    assert settler.tick() == 0
    assert ChecksSettler(store.peer(), lister, clock=clock).tick() == 0
    assert len(settled(store)) == 1


def test_recovery_keeps_the_webhook_deadline_semantics_for_a_late_recovery():
    store, lister, clock, settler = make(("a", "in_progress"))
    stored_check(store, clock)
    clock.now += timedelta(seconds=901)  # recovered only after the timeout would have hit
    assert settler.tick() == 1
    [event] = settled(store)
    assert event["envelope"]["data"]["settled_by"] == "timeout"


def test_recovery_never_rearms_a_sha_settled_by_completion_or_timeout():
    for status in ("completed", "in_progress"):
        store, lister, clock, settler = make(("a", status))
        settler.on_check(check_data())
        clock.now += timedelta(seconds=901)
        settler.tick()
        record = store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}")
        assert record["state"] == "emitted"
        # a later completion of the same SHA (a re-run) is stored but never re-arms it
        stored_check(store, clock, "rerun")
        # even without the settled event, the terminal record is authoritative
        store.delete(EVENTS_COLLECTION, settled_event_id(REPO, SHA))
        calls = lister.calls
        past_grace(clock)
        assert settler.tick() == 0
        assert store.get(SETTLE_COLLECTION, record["id"]) == record
        assert lister.calls == calls
        assert settled(store) == []


def test_recovery_never_rearms_a_sha_whose_settled_event_exists():
    store, lister, clock, settler = make(("a", "completed"))
    settler.on_check(check_data())
    assert len(settled(store)) == 1
    store.delete(SETTLE_COLLECTION, f"{REPO}@{SHA}")  # only the settled event remains
    stored_check(store, clock)
    past_grace(clock)
    assert settler.tick() == 0
    assert store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}") is None
    assert len(settled(store)) == 1


def test_recovery_scan_is_windowed_capped_per_tick_and_advances_a_shared_watermark(
    monkeypatch,
):
    import culture_rules.node.checks_settle as module

    monkeypatch.setattr(module, "RECOVERY_BATCH", 2)
    store, lister, clock, settler = make(("a", "completed"))
    clock.now -= timedelta(seconds=module.RECOVERY_WINDOW_S + 60)
    stored_check(store, clock, "ancient", head_sha="old")  # outside the scan window
    clock.now += timedelta(seconds=module.RECOVERY_WINDOW_S + 60)
    for n in range(5):
        stored_check(store, clock, f"check-{n}", head_sha=f"{n}" * 40)
        clock.now += timedelta(microseconds=1)
    calls = []
    original = store.find_events

    def spy(**kwargs):
        result = original(**kwargs)
        calls.append((kwargs, len(result)))
        return result

    monkeypatch.setattr(store, "find_events", spy)
    past_grace(clock)
    other = ChecksSettler(store.peer(), lister, clock=clock)
    assert settler.tick() == 2
    assert other.tick() == 2  # a second node continues from the shared watermark
    assert settler.tick() == 1
    assert other.tick() == 0
    assert len(settled(store)) == 5
    assert all(kw["limit"] == 2 and n <= 2 for kw, n in calls)
    assert store.get(SETTLE_COLLECTION, f"{REPO}@old") is None
    mark = store.get(module.RECOVERY_COLLECTION, module.RECOVERY_ID)
    assert mark["event_id"] == "check-4"
    assert settler.tick() == 0
    assert calls[-1] == ({**calls[-1][0], "after": (mark["received_at"], "check-4")}, 0)


def test_recovery_store_error_holds_the_watermark_and_the_next_tick_recovers(monkeypatch):
    from culture_rules.store.port import StoreError

    store, lister, clock, settler = make(("a", "completed"))
    stored_check(store, clock, "first", head_sha="1" * 40)
    clock.now += timedelta(microseconds=1)
    stored_check(store, clock, "second", head_sha="2" * 40)
    original = store.insert
    failing = {"2" * 40}

    def flaky(collection, document):
        if collection == SETTLE_COLLECTION and document["head_sha"] in failing:
            raise StoreError("temporary outage")
        return original(collection, document)

    monkeypatch.setattr(store, "insert", flaky)
    past_grace(clock)
    assert settler.tick() == 1  # the first recovers; the failed second stays unhandled
    assert store.get(settler_mark(), "recovery")["event_id"] == "first"
    failing.clear()
    assert settler.tick() == 1
    assert settler.tick() == 0
    assert len(settled(store)) == 2


def settler_mark():
    from culture_rules.node.checks_settle import RECOVERY_COLLECTION

    return RECOVERY_COLLECTION


def test_recovery_failure_never_blocks_the_pending_polls(monkeypatch):
    store, lister, clock, settler = make(("a", "in_progress"))
    settler.on_check(check_data())

    def broken(**_):
        raise RuntimeError("driver error")

    monkeypatch.setattr(store, "find_events", broken)
    clock.now += timedelta(seconds=901)
    assert settler.tick() == 1  # the timeout still fires


def test_concurrent_nodes_recover_and_fire_a_sha_exactly_once():
    import threading

    store, lister, clock, _ = make(("a", "completed"))
    for n in range(20):
        stored_check(store, clock, f"c{n}", head_sha=f"{n:040d}")
    past_grace(clock)
    nodes = [ChecksSettler(store.peer(), lister, clock=clock) for _ in range(4)]
    barrier = threading.Barrier(len(nodes))
    totals = []

    def run(node):
        barrier.wait()
        totals.append(sum(node.tick() for _ in range(3)))

    threads = [threading.Thread(target=run, args=(node,)) for node in nodes]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(totals) == 20
    assert len(settled(store)) == 20
    assert len({e["id"] for e in settled(store)}) == 20


# ---------------------------------------------------------------------- placement (wave-3 P1)


def _placed_store(machine):
    store = MemoryStore()
    store.put_variable("checks_settle_min_s", 0, updated_by="t")
    store.put_variable("checks_settle_timeout_s", 60, updated_by="t")
    store.insert(
        "actors",
        {
            "id": "github-app",
            "kind": "app",
            "machine": machine,
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


def _github(suite_status):
    import json

    pr = {
        "number": 7,
        "head": {"sha": "b" * 40, "ref": "feat", "repo": {"full_name": REPO}},
        "base": {"sha": "c" * 40, "ref": "main", "repo": {"full_name": REPO}},
        "draft": False,
        "user": {"login": "alice"},
    }
    calls = []

    def transport(method, url, headers, body, timeout):
        calls.append(url)
        if url.endswith("/access_tokens"):
            expires = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": "t", "expires_at": expires}).encode()
        if "/check-suites" in url:
            suite = {"app": {"slug": "github-actions"}, "status": suite_status[0]}
            suite["conclusion"] = "success" if suite_status[0] == "completed" else None
            return 200, json.dumps({"check_suites": [suite]}).encode()
        return 200, json.dumps(pr).encode()

    return transport, calls


class NoSecret:
    """A node without the App key: every resolve fails (and is counted)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, ref):
        from culture_rules.actors.secrets import SecretError

        self.calls += 1
        raise SecretError(f"cannot resolve secret reference {ref}")


def _node_settler(store, host, secrets, transport, clock, **kw):
    from culture_rules.node.checks_settle import AppSuiteLister

    lister = AppSuiteLister(store, host=host, transport=transport, secrets=secrets, **kw)
    return ChecksSettler(
        store, lister.list_suites, pull=lister.get_pull, serves=lister.serves, clock=clock
    )


def _arm_pending(store, clock):
    """The webhook armed the SHA while a suite still ran (the server lists via its own key)."""
    webhook = ChecksSettler(store, Suites(("github-actions", "in_progress")), clock=clock)
    assert webhook.on_check(check_data()) == "pending"


def test_settle_polls_only_on_the_app_actors_machine_with_enrichment():
    import pytest

    pytest.importorskip("cryptography")
    store, clock, status = _placed_store("spark"), Clock(), ["in_progress"]
    transport, calls = _github(status)
    _arm_pending(store, clock)
    # thor has no key; spark2 has the key but the actor is placed on spark
    thor = _node_settler(store.peer(), "thor", NoSecret(), transport, clock)
    pem = _pem()
    spark2 = _node_settler(store.peer(), "spark2", lambda ref: pem, transport, clock)
    spark = _node_settler(store.peer(), "spark", lambda ref: pem, transport, clock)
    status[0] = "completed"
    clock.now = T0 + timedelta(seconds=61)  # past the deadline: a claim here would time out
    for _ in range(5):
        assert thor.tick() == 0
        assert spark2.tick() == 0
    rec = store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}")
    assert rec["state"] == "pending" and not rec.get("polls") and calls == []
    assert settled(store) == []
    assert spark.tick() == 1
    [event] = settled(store)
    data = event["envelope"]["data"]
    assert data["settled_by"] == "all_completed" and data["conclusion"] == "success"
    assert data["head_repo"] == data["base_repo"] == REPO and data["base_sha"] == "c" * 40


def test_settle_fails_closed_on_the_placed_machine_without_the_key():
    store, clock = _placed_store("spark"), Clock()
    transport, calls = _github(["in_progress"])
    _arm_pending(store, clock)
    spark = _node_settler(store.peer(), "spark", NoSecret(), transport, clock)
    clock.now = T0 + timedelta(seconds=61)
    assert spark.tick() == 0
    assert settled(store) == [] and calls == []
    assert not store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}").get("polls")


def test_unplaced_app_actor_settles_on_any_node_that_resolves_its_key():
    import pytest

    pytest.importorskip("cryptography")
    store, clock, status = _placed_store(None), Clock(), ["completed"]
    transport, _ = _github(status)
    _arm_pending(store, clock)
    thor = _node_settler(store.peer(), "thor", NoSecret(), transport, clock)
    pem = _pem()
    spark2 = _node_settler(store.peer(), "spark2", lambda ref: pem, transport, clock)
    clock.now = T0 + timedelta(seconds=61)
    assert thor.tick() == 0 and settled(store) == []
    assert spark2.tick() == 1
    assert settled(store)[0]["envelope"]["data"]["head_repo"] == REPO


def test_a_node_without_the_key_does_not_retry_the_resolve_every_cycle():
    store, clock = _placed_store(None), Clock()
    transport, _ = _github(["in_progress"])
    _arm_pending(store, clock)
    secrets = NoSecret()
    thor = _node_settler(store.peer(), "thor", secrets, transport, clock, unresolved_retry_s=3600)
    clock.now = T0 + timedelta(seconds=61)
    for _ in range(10):
        thor.tick()
    assert secrets.calls == 1


def test_node_wires_the_settler_to_its_own_machine():
    from culture_rules.node.daemon import Node

    store, clock = _placed_store(None), Clock()
    _arm_pending(store, clock)
    node = Node(store, "thor", clock=clock, resolve_secret=NoSecret())
    clock.now = T0 + timedelta(seconds=61)
    assert node.settler.tick() == 0
    assert settled(store) == []
    assert not store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}").get("polls")


def test_no_counted_suites_never_settles_green_and_times_out_as_no_checks():
    """Review #17 finding 3: with every listed suite from an ignored app (or none listed
    yet) nothing is green - the SHA keeps waiting and settles at the timeout with the
    distinct conclusion ``no_checks``, which does not reset a rule's attempt budget."""
    from culture_rules.node.firing import _resets_budgets

    for suites in ([("claude", "completed")], []):
        store, _, clock, settler = make(*suites)
        store.put_variable("checks_settle_timeout_s", 60, updated_by="t")
        assert settler.on_check(check_data()) == "pending"
        clock.now = T0 + timedelta(seconds=30)
        assert settler.tick() == 0 and settled(store) == []
        clock.now = T0 + timedelta(seconds=61)
        assert settler.tick() == 1
        [event] = settled(store)
        assert event["envelope"]["data"]["settled_by"] == "timeout"
        assert event["envelope"]["data"]["conclusion"] == "no_checks"
        assert _resets_budgets(event["envelope"]) is False


def test_a_suite_appearing_before_the_timeout_still_settles_normally():
    store, lister, clock, settler = make(("claude", "completed"))
    assert settler.on_check(check_data()) == "pending"
    lister.suites["github-actions"] = "completed"
    clock.now = T0 + timedelta(seconds=20)
    assert settler.on_check(check_data()) == "emitted"
    assert settled(store)[0]["envelope"]["data"]["conclusion"] == "failure"


# --------------------------------- review #17 finding 5: the webhook settle path is bounded


def _suites_transport(method, url, headers, body, timeout):
    import json

    if "/check-suites" in url:
        suite = {"app": {"slug": "ci"}, "status": "completed", "conclusion": "failure"}
        return 200, json.dumps({"check_suites": [suite]}).encode()
    return _ok_transport(method, url, headers, body, timeout)


def test_app_lister_list_suites_is_bounded_like_get_pull():
    import time

    import pytest

    pytest.importorskip("cryptography")
    from culture_rules.node.checks_settle import AppSuiteLister

    secrets = SlowSecrets(_pem())
    lister = AppSuiteLister(_lister_store(), transport=_suites_transport, secrets=secrets)
    try:
        started = time.monotonic()
        with pytest.raises(GitHubError) as err:
            lister.list_suites(REPO, SHA, timeout_s=0.2)
        assert err.value.code == "deadline_exceeded" and err.value.retryable
        assert time.monotonic() - started < 2
        with pytest.raises(GitHubError) as err:
            lister.list_suites(REPO, SHA, timeout_s=0)  # no budget left: no worker started
        assert err.value.code in ("deadline_exceeded", "lookup_busy")
    finally:
        secrets.release.set()


def test_webhook_on_check_answers_within_its_budget_and_leaves_the_settle_pending():
    """A cold ``grant get`` on the webhook path: on_check returns within the budget, the
    SHA stays armed (pending) and the node tick settles it later."""
    import time

    import pytest

    pytest.importorskip("cryptography")
    from culture_rules.node.checks_settle import AppSuiteLister, webhook_on_check

    store = _lister_store()
    store.put_variable("checks_settle_min_s", 0, updated_by="t")
    secrets = SlowSecrets(_pem())
    lister = AppSuiteLister(store, transport=_suites_transport, secrets=secrets)
    try:
        on_check = webhook_on_check(store, lister, budget_s=0.3)
        started = time.monotonic()
        assert on_check(check_data()) == "error"
        assert time.monotonic() - started < 2
        assert store.get(SETTLE_COLLECTION, f"{REPO}@{SHA}")["state"] == "pending"
        assert settled(store) == []
    finally:
        secrets.release.set()


def test_webhook_settle_defers_to_the_tick_when_the_pr_lookup_times_out():
    """The suites list in time but the PR read does not: the webhook does not emit a
    settled event without the PR facts (the fixer condition would never match it); the
    SHA stays pending for the node tick, whose emit carries them."""
    from culture_rules.node.checks_settle import webhook_on_check

    class Lister:
        def list_suites(self, repo, sha, *, timeout_s=None):
            assert timeout_s is not None and timeout_s > 0
            return [{"app_slug": "ci", "status": "completed", "conclusion": "failure"}]

        def get_pull(self, repo, number, *, timeout_s=None):
            assert timeout_s is not None
            raise GitHubError("deadline_exceeded", retryable=True)

    store = MemoryStore()
    store.put_variable("checks_settle_min_s", 0, updated_by="t")
    assert webhook_on_check(store, Lister())(check_data()) == "pending"
    assert settled(store) == []
    pr = {
        "head": {"sha": SHA, "ref": "feat", "repo": {"full_name": REPO}},
        "base": {"sha": "c" * 40, "ref": "main", "repo": {"full_name": REPO}},
        "draft": False,
        "user": {"login": "alice"},
    }
    node = ChecksSettler(
        store,
        lambda r, s: Lister().list_suites(r, s, timeout_s=1),
        pull=lambda r, n: pr,
        clock=lambda: datetime.now(UTC) + timedelta(hours=1),
    )
    assert node.tick() == 1
    assert settled(store)[0]["envelope"]["data"]["base_repo"] == REPO


def test_api_server_wires_the_bounded_webhook_settle(monkeypatch):
    from fastapi import FastAPI

    import culture_rules.node.checks_settle as cs
    from culture_rules.server import app as server_app

    seen = {}

    def fake(store, lister, **kw):
        seen["lister"] = lister
        return lambda data: "pending"

    monkeypatch.setattr(cs, "webhook_on_check", fake)
    server_app._register_hooks(FastAPI(), MemoryStore())
    assert isinstance(seen["lister"], cs.AppSuiteLister)


# --------------------------------------------------------------------------- d20 round 5


def test_rearm_settles_the_same_head_again_in_a_new_generation_then_dedupes():
    from culture_rules.node.checks_settle import rearm_settle

    store, _, clock, settler = make(("a", "completed"))
    assert settler.on_check(check_data()) == "emitted"
    assert settler.on_check(check_data()) == "duplicate"
    assert rearm_settle(store, REPO, SHA, reason="base_changed", now=clock()) == "rearmed"
    assert rearm_settle(store, REPO, SHA, reason="base_changed", now=clock()) == "pending"
    assert settler.on_check(check_data()) == "emitted"  # generation 1
    assert settler.on_check(check_data()) == "duplicate"
    ids = {e["id"] for e in settled(store)}
    assert ids == {settled_event_id(REPO, SHA), settled_event_id(REPO, SHA, 1)}
    assert settled_event_id(REPO, SHA, 0) == settled_event_id(REPO, SHA)  # unchanged ids


def test_a_re_armed_head_also_settles_from_the_nodes_poll():
    from culture_rules.node.checks_settle import rearm_settle

    store, _, clock, settler = make(("a", "completed"))
    settler.on_check(check_data())
    rearm_settle(store, REPO, SHA, reason="base_changed", now=clock())
    assert settler.tick() == 1  # no new check completion needed
    assert len(settled(store)) == 2


def test_rearming_is_bounded():
    from culture_rules.node.checks_settle import REARM_LIMIT, rearm_settle

    store, _, clock, settler = make(("a", "completed"))
    settler.on_check(check_data())
    for _ in range(REARM_LIMIT):
        assert rearm_settle(store, REPO, SHA, reason="base_changed", now=clock()) == "rearmed"
        assert settler.on_check(check_data()) == "emitted"
    assert rearm_settle(store, REPO, SHA, reason="base_changed", now=clock()) == "limit"
    assert settler.on_check(check_data()) == "duplicate"
    assert len(settled(store)) == REARM_LIMIT + 1


def test_rearming_a_head_never_settled_arms_it():
    from culture_rules.node.checks_settle import rearm_settle

    store, _, clock, settler = make(("a", "completed"))
    assert rearm_settle(store, REPO, SHA, reason="base_changed", now=clock()) == "armed"
    assert settler.tick() == 1 and len(settled(store)) == 1
