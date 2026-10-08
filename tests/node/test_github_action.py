"""github.comment action port: allowlist, secret refs, result mapping (fake transport)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from culture_rules.engine.actorport import InvocationContext  # noqa: E402
from culture_rules.node.actions.github import GitHubCommentPort  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402

FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"
DEADLINE = datetime(2030, 1, 1, tzinfo=UTC)


@pytest.fixture(scope="module")
def pem():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class Fake:
    def __init__(self, status=201):
        self.calls = []
        self.status = status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(url)
        if url.endswith("/access_tokens"):
            exp = (datetime.now(UTC) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            return 201, json.dumps({"token": FAKE_BEARER, "expires_at": exp}).encode()
        if self.status != 201:
            return self.status, b"{}"
        return 201, json.dumps({"id": 9, "html_url": "https://x/9"}).encode()


def actor_doc(**conn):
    connection = {
        "app_id": "1",
        "installation_id": "2",
        "private_key": "grant:GH_KEY",
        "webhook_secret": "grant:GH_HOOK",
        "repos": ["acme/widgets"],
    }
    connection.update(conn)
    return {
        "id": "gh-app",
        "name": "gh",
        "kind": "service",
        "params": {"surface": "github", "connection": connection},
        "schema_version": "1.0",
    }


def setup(pem, fake, doc=None):
    store = MemoryStore()
    store.put("actors", doc or actor_doc())
    resolved = []

    def secrets(ref):
        resolved.append(ref)
        return pem

    port = GitHubCommentPort(store, transport=fake, secrets=secrets)
    return port, resolved


def ctx():
    return InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor="gh-app")


def params(repo="acme/widgets"):
    return {"actor": "gh-app", "repo": repo, "number": 3, "body": "hi"}


def test_port_flags():
    assert GitHubCommentPort(MemoryStore()).supports_idempotency_key is False


def test_allowlisted_comment_completes(pem):
    fake = Fake()
    port, resolved = setup(pem, fake)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert dict(res.output) == {"comment_id": 9, "url": "https://x/9"}
    assert fake.calls[-1].endswith("/repos/acme/widgets/issues/3/comments")
    assert resolved == ["grant:GH_KEY"]


def test_not_allowlisted_fails_without_network_or_secret(pem, caplog):
    caplog.set_level(logging.DEBUG)
    fake = Fake()
    port, resolved = setup(pem, fake)
    res = port.invoke(params("evil/repo"), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert res.error == "repo_not_allowed"
    assert not res.retryable
    assert fake.calls == []
    assert resolved == []
    assert FAKE_BEARER not in caplog.text


def test_missing_actor_fails(pem):
    port, _ = setup(pem, Fake())
    c = InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor="nope")
    res = port.invoke(params(), "k", DEADLINE, context=c)
    assert res.outcome == "failed"
    assert not res.retryable


def test_5xx_retryable_4xx_not(pem):
    port, _ = setup(pem, Fake(status=502))
    assert port.invoke(params(), "k", DEADLINE, context=ctx()).retryable is True
    port, _ = setup(pem, Fake(status=422))
    assert port.invoke(params(), "k", DEADLINE, context=ctx()).retryable is False


def test_secret_failure_is_nonretryable_and_quiet(pem, caplog):
    store = MemoryStore()
    store.put("actors", actor_doc())

    def secrets(ref):
        raise RuntimeError("boom")

    port = GitHubCommentPort(store, transport=Fake(), secrets=secrets)
    res = port.invoke(params(), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert not res.retryable


def test_token_not_in_logs(pem, caplog):
    caplog.set_level(logging.DEBUG)
    port, _ = setup(pem, Fake())
    port.invoke(params(), "k", DEADLINE, context=ctx())
    assert FAKE_BEARER not in caplog.text
    assert "PRIVATE KEY" not in caplog.text


def test_app_and_token_cached_across_invocations(pem):
    fake = Fake()
    port, resolved = setup(pem, fake)
    port.invoke(params(), "k1", DEADLINE, context=ctx())
    port.invoke(params(), "k2", DEADLINE, context=ctx())
    assert resolved == ["grant:GH_KEY"]
    assert sum(u.endswith("/access_tokens") for u in fake.calls) == 1


@pytest.mark.parametrize(
    "conn",
    [
        {"repos": ["acme/widgets/../x"]},
        {"app_id": ""},
        {"installation_id": None},
    ],
)
def test_a_misconfigured_actor_fails_before_any_secret_or_network(pem, conn):
    fake = Fake()
    port, resolved = setup(pem, fake, actor_doc(**conn))
    repo = conn.get("repos", ["acme/widgets"])[0]
    res = port.invoke(params(repo), "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    assert not res.retryable
    assert fake.calls == []
    assert resolved == []


def test_a_disabled_actor_drops_its_cached_app(pem):
    store = MemoryStore()
    store.put("actors", actor_doc())
    port = GitHubCommentPort(store, transport=Fake(), secrets=lambda ref: pem)
    port.invoke(params(), "k1", DEADLINE, context=ctx())
    store.put("actors", {**actor_doc(), "enabled": False})
    assert port.invoke(params(), "k2", DEADLINE, context=ctx()).error == "actor_not_found"
    assert "gh-app" not in port._apps


class HeadFake(Fake):
    def __init__(self, sha="c" * 40, status=200):
        super().__init__(status)
        self.sha = sha

    def __call__(self, method, url, headers, body, timeout):
        if "/pulls/" in url:
            self.calls.append(url)
            if self.status != 200:
                return self.status, b"{}"
            return 200, json.dumps({"head": {"sha": self.sha}}).encode()
        return super().__call__(method, url, headers, body, timeout)


def test_pr_head_port_reads_the_head_sha(pem):
    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    store.put("actors", actor_doc())
    fake = HeadFake()
    port = GitHubPrHeadPort(store, transport=fake, secrets=lambda ref: pem)
    res = port.invoke({"repo": "acme/widgets", "number": 3}, "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert dict(res.output) == {"head_sha": "c" * 40, "base_sha": None}
    assert fake.calls[-1].endswith("/repos/acme/widgets/pulls/3")


def test_pr_head_port_refuses_unlisted_repo_and_surfaces_errors(pem):
    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    store.put("actors", actor_doc())
    fake = HeadFake(status=500)
    port = GitHubPrHeadPort(store, transport=fake, secrets=lambda ref: pem)
    bad = port.invoke({"repo": "evil/repo", "number": 3}, "k", DEADLINE, context=ctx())
    assert (bad.outcome, bad.error) == ("failed", "repo_not_allowed")
    assert fake.calls == []
    res = port.invoke({"repo": "acme/widgets", "number": 3}, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"


class SlowSecrets:
    """A ``grant get`` stand-in that blocks until released (a cold, slow resolve)."""

    def __init__(self, pem):
        import threading

        self.pem = pem
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, ref):
        self.calls += 1
        self.release.wait(10)
        return self.pem


def test_pr_head_port_honours_the_deadline_through_a_cold_secret_resolve(pem):
    """Review #17 finding 6: the head lookup runs inside the executor tick, so a slow
    ``grant get`` must not hold it past the invocation deadline. It answers
    ``deadline_exceeded`` (retryable) at the deadline; the resolve finishes in the
    background and caches the App, so the next lookup is warm (one resolve in all)."""
    import time

    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    store.put("actors", actor_doc())
    fake = HeadFake()
    secrets = SlowSecrets(pem)
    port = GitHubPrHeadPort(store, transport=fake, secrets=secrets)
    try:
        started = time.monotonic()
        soon = datetime.now(UTC) + timedelta(seconds=0.2)
        res = port.invoke({"repo": "acme/widgets", "number": 3}, "k", soon, context=ctx())
        assert (res.outcome, res.error, res.retryable) == ("failed", "deadline_exceeded", True)
        assert time.monotonic() - started < 2
    finally:
        secrets.release.set()
    give_up = time.monotonic() + 5
    while "gh-app" not in port._apps:  # the timed-out worker warms the per-actor App cache
        assert time.monotonic() < give_up
        time.sleep(0.01)
    while True:
        later = datetime.now(UTC) + timedelta(seconds=2)
        res = port.invoke({"repo": "acme/widgets", "number": 3}, "k", later, context=ctx())
        if res.outcome == "completed" or time.monotonic() > give_up:
            break
        assert res.error in ("deadline_exceeded", "lookup_busy")
        time.sleep(0.02)
    assert dict(res.output) == {"head_sha": "c" * 40, "base_sha": None}
    assert secrets.calls == 1


def test_pr_head_port_bounds_its_http_calls_by_the_deadline(pem):
    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    store.put("actors", actor_doc())
    fake = HeadFake()
    port = GitHubPrHeadPort(store, transport=fake, secrets=lambda ref: pem)
    warm = port.invoke({"repo": "acme/widgets", "number": 3}, "k", DEADLINE, context=ctx())
    assert warm.outcome == "completed"
    fake.calls.clear()
    past = datetime.now(UTC) - timedelta(seconds=1)
    res = port.invoke({"repo": "acme/widgets", "number": 3}, "k", past, context=ctx())
    assert (res.outcome, res.error, res.retryable) == ("failed", "deadline_exceeded", True)
    assert fake.calls == []  # no network call started past the deadline


class HangingHeadFake(HeadFake):
    """GitHub accepting the PR read but never answering: the transport times out after the
    timeout it was given, as urllib does (``TimeoutError``, wrapped in ``URLError``)."""

    def __init__(self, wrap=False):
        super().__init__()
        self.wrap = wrap
        self.timeouts = []

    def __call__(self, method, url, headers, body, timeout):
        import time
        from urllib.error import URLError

        if "/pulls/" in url:
            self.timeouts.append(timeout)
            time.sleep(timeout)
            exc = TimeoutError("timed out")
            raise URLError(exc) if self.wrap else exc
        return super().__call__(method, url, headers, body, timeout)


@pytest.mark.parametrize("wrap", [False, True])
def test_pr_head_port_turns_an_in_flight_http_timeout_into_deadline_exceeded(pem, wrap):
    """Codex r17b finding 2: a read cut off in flight by the deadline-bound timeout is
    ``deadline_exceeded`` (retryable, which the wait guard re-arms on), not a
    ``network_error`` that fails the run at once."""
    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    store.put("actors", actor_doc())
    fake = HangingHeadFake(wrap=wrap)
    port = GitHubPrHeadPort(store, transport=fake, secrets=lambda ref: pem)
    soon = datetime.now(UTC) + timedelta(seconds=0.3)
    res = port.invoke({"repo": "acme/widgets", "number": 3}, "k", soon, context=ctx())
    assert (res.outcome, res.error, res.retryable) == ("failed", "deadline_exceeded", True)
    assert fake.timeouts
    assert fake.timeouts[0] <= 0.3


def test_a_transport_failure_unrelated_to_the_deadline_stays_a_network_error(pem):
    from culture_rules.node.actions.github import GitHubPrHeadPort

    class Refused(HeadFake):
        def __call__(self, method, url, headers, body, timeout):
            if "/pulls/" in url:
                raise ConnectionRefusedError("no")
            return super().__call__(method, url, headers, body, timeout)

    store = MemoryStore()
    store.put("actors", actor_doc())
    port = GitHubPrHeadPort(store, transport=Refused(), secrets=lambda ref: pem)
    res = port.invoke({"repo": "acme/widgets", "number": 3}, "k", DEADLINE, context=ctx())
    assert (res.outcome, res.error) == ("failed", "network_error")


# ---------------------------------------------------------------- characterization
# (the Sonar S3776 split of GitHubPrHeadPort.invoke: every outcome, pinned)


def _head_port(pem, doc=None, fake=None, secrets=None):
    from culture_rules.node.actions.github import GitHubPrHeadPort

    store = MemoryStore()
    if doc is not False:
        store.put("actors", doc or actor_doc())
    fake = fake or HeadFake()
    port = GitHubPrHeadPort(store, transport=fake, secrets=secrets or (lambda ref: pem))
    return port, fake


def _ask(port, **inp):
    return port.invoke({"repo": "acme/widgets", "number": 3, **inp}, "k", DEADLINE, context=ctx())


def test_pr_head_port_without_the_actor_is_actor_not_found(pem):
    port, fake = _head_port(pem, doc=False)
    port._apps["gh-app"] = ((), object())
    res = _ask(port)
    assert (res.outcome, res.error, res.retryable) == ("failed", "actor_not_found", False)
    assert "gh-app" not in port._apps
    assert fake.calls == []


@pytest.mark.parametrize("conn", [{"app_id": ""}, {"installation_id": None}])
def test_pr_head_port_misconfigured_actor(pem, conn):
    port, fake = _head_port(pem, doc=actor_doc(**conn))
    res = _ask(port)
    assert (res.error, res.retryable) == ("actor_misconfigured", False)
    assert fake.calls == []


@pytest.mark.parametrize("number", [None, "x", [3]])
def test_pr_head_port_bad_number_is_bad_input(pem, number):
    port, fake = _head_port(pem)
    res = _ask(port, number=number)
    assert (res.error, res.retryable) == ("bad_input", False)
    assert fake.calls == []


def test_pr_head_port_missing_number_is_bad_input(pem):
    port, _ = _head_port(pem)
    res = port.invoke({"repo": "acme/widgets"}, "k", DEADLINE, context=ctx())
    assert (res.error, res.retryable) == ("bad_input", False)


def test_pr_head_port_secret_failure_is_secret_unavailable(pem):
    def boom(ref):
        raise RuntimeError("no grant")

    port, fake = _head_port(pem, secrets=boom)
    res = _ask(port)
    assert (res.error, res.retryable) == ("secret_unavailable", False)
    assert fake.calls == []


@pytest.mark.parametrize("sha", [None, "", 7])
def test_pr_head_port_without_a_head_sha_is_a_retryable_bad_response(pem, sha):
    port, _ = _head_port(pem, fake=HeadFake(sha=sha))
    res = _ask(port)
    assert (res.error, res.retryable) == ("bad_response", True)


def test_pr_head_port_reports_a_string_base_sha_only(pem):
    class BaseFake(HeadFake):
        def __init__(self, base):
            super().__init__()
            self.base = base

        def __call__(self, method, url, headers, body, timeout):
            if "/pulls/" in url:
                doc = {"head": {"sha": self.sha}, "base": {"sha": self.base}}
                return 200, json.dumps(doc).encode()
            return super().__call__(method, url, headers, body, timeout)

    port, _ = _head_port(pem, fake=BaseFake("d" * 40))
    assert dict(_ask(port).output) == {"head_sha": "c" * 40, "base_sha": "d" * 40}
    port, _ = _head_port(pem, fake=BaseFake(5))
    assert dict(_ask(port).output) == {"head_sha": "c" * 40, "base_sha": None}


def test_pr_head_port_http_error_keeps_its_code_and_retryability(pem):
    port, _ = _head_port(pem, fake=HeadFake(status=500))
    res = _ask(port)
    assert (res.error, res.retryable) == ("http_500", True)
    port, _ = _head_port(pem, fake=HeadFake(status=404))
    res = _ask(port)
    assert res.outcome == "failed"
    assert res.retryable is False


# --------------------------------------------------------------------------- once_key (d25)


def _comments(fake) -> int:
    return sum(1 for url in fake.calls if url.endswith("/comments"))


def test_a_once_key_posts_once_per_repo_and_pr_durably_across_ports(pem):
    fake = Fake()
    port, _ = setup(pem, fake)
    once = {**params(), "once_key": "gitguardian@" + "a" * 40}
    first = port.invoke(once, "k1", DEADLINE, context=ctx())
    assert dict(first.output) == {"comment_id": 9, "url": "https://x/9"}
    again = port.invoke(once, "k2", DEADLINE, context=ctx())
    assert again.outcome == "completed"
    assert dict(again.output) == {"comment_id": 9, "url": "https://x/9", "skipped": "posted_before"}
    # another node, another process: the claim is in the store
    other = GitHubCommentPort(port._store, transport=fake, secrets=lambda ref: pem)
    assert other.invoke(once, "k3", DEADLINE, context=ctx()).output["skipped"] == "posted_before"
    assert _comments(fake) == 1


def test_a_once_key_is_scoped_to_its_pr_and_absent_keys_always_post(pem):
    fake = Fake()
    port, _ = setup(pem, fake)
    port.invoke({**params(), "once_key": "x"}, "k", DEADLINE, context=ctx())
    port.invoke({**params(), "number": 4, "once_key": "x"}, "k", DEADLINE, context=ctx())
    port.invoke(params(), "k", DEADLINE, context=ctx())
    port.invoke(params(), "k", DEADLINE, context=ctx())
    assert _comments(fake) == 4


@pytest.mark.parametrize("status", [400, 403, 404, 422, 429])
def test_a_refused_post_releases_its_once_key(pem, status):
    # GitHub answered with a client error: no comment exists, a later firing may post
    fake = Fake(status=status)
    port, _ = setup(pem, fake)
    once = {**params(), "once_key": "x"}
    res = port.invoke(once, "k", DEADLINE, context=ctx())
    assert res.outcome == "failed"
    fake.status = 201
    assert dict(port.invoke(once, "k", DEADLINE, context=ctx()).output)["comment_id"] == 9


@pytest.mark.parametrize("status", [408, 500, 502, 503])
def test_an_ambiguous_post_keeps_its_once_key(pem, status):
    # the comment may exist (a timeout, a 5xx after the write): at most once, never twice
    fake = Fake(status=status)
    port, _ = setup(pem, fake)
    once = {**params(), "once_key": "x"}
    assert port.invoke(once, "k", DEADLINE, context=ctx()).outcome == "failed"
    fake.status = 201
    again = port.invoke(once, "k", DEADLINE, context=ctx())
    assert again.outcome == "completed"
    assert again.output["skipped"] == "claimed_before"
    assert _comments(fake) == 1  # the one ambiguous attempt only


def test_a_network_error_on_the_post_keeps_its_once_key(pem):
    class Drops(Fake):
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith("/comments"):
                self.calls.append(url)
                raise ConnectionResetError("reset after send")
            return super().__call__(method, url, headers, body, timeout)

    fake = Drops()
    port, _ = setup(pem, fake)
    once = {**params(), "once_key": "x"}
    res = port.invoke(once, "k", DEADLINE, context=ctx())
    assert res.error == "network_error"
    assert port.invoke(once, "k", DEADLINE, context=ctx()).output["skipped"] == "claimed_before"


def test_a_token_failure_happens_before_the_claim(pem):
    # nothing was sent to the comments endpoint: no claim is taken, a retry may post
    class NoToken(Fake):
        def __call__(self, method, url, headers, body, timeout):
            if url.endswith("/access_tokens"):
                self.calls.append(url)
                raise ConnectionResetError("down")
            return super().__call__(method, url, headers, body, timeout)

    fake = NoToken()
    port, _ = setup(pem, fake)
    once = {**params(), "once_key": "x"}
    assert port.invoke(once, "k", DEADLINE, context=ctx()).error == "network_error"
    assert port._store.find("github_comment_once") == []


def test_a_once_key_that_is_not_a_non_empty_string_is_bad_input(pem):
    fake = Fake()
    port, _ = setup(pem, fake)
    res = port.invoke({**params(), "once_key": ""}, "k", DEADLINE, context=ctx())
    assert res.error == "bad_input"
    assert _comments(fake) == 0


# --------------------------------------------------------------------------- d26: status


@pytest.mark.parametrize("status", ["yes", 1, None])
def test_status_must_be_a_boolean(pem, status):
    fake = Fake()
    port, _ = setup(pem, fake)
    res = port.invoke({**params(), "status": status}, "k", DEADLINE, context=ctx())
    assert res.error == "bad_input"
    assert fake.calls == []


def test_status_and_once_key_do_not_combine(pem):
    fake = Fake()
    port, _ = setup(pem, fake)
    given = {**params(), "status": True, "once_key": "k1"}
    res = port.invoke(given, "k", DEADLINE, context=ctx())
    assert res.error == "bad_input"
    assert fake.calls == []


def test_status_outside_a_status_chain_posts_a_plain_comment(pem):
    fake = Fake()
    port, _ = setup(pem, fake)  # run "r" does not exist: no chain to write into
    res = port.invoke({**params(), "status": True}, "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"
    assert dict(res.output) == {"comment_id": 9, "url": "https://x/9"}
    assert fake.calls[-1].endswith("/repos/acme/widgets/issues/3/comments")


def test_status_false_is_an_ordinary_comment(pem):
    fake = Fake()
    port, _ = setup(pem, fake)
    res = port.invoke({**params(), "status": False}, "k", DEADLINE, context=ctx())
    assert res.outcome == "completed"


def test_the_reporter_acts_only_for_the_app_actors_on_its_host(pem):
    port, _ = setup(pem, Fake(), doc={**actor_doc(), "machine": "spark"})
    assert port._serves("gh-app", "spark") is True
    assert port._serves("gh-app", "spark2") is False
    assert port._serves("missing", "spark") is False
    unplaced, _ = setup(pem, Fake())
    assert unplaced._serves("gh-app", "anywhere") is True
    assert unplaced.status_tick("spark") == 0  # no runs: nothing to post
