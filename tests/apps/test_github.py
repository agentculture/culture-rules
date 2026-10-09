"""GitHub App client: JWT, cached installation token, allowlisted comments (fake transport)."""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest

pytest.importorskip("cryptography")

from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import padding, rsa  # noqa: E402

from culture_rules.apps.github import GitHubApp, GitHubError  # noqa: E402

NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)
FAKE_BEARER = "ghs" + "_" + "FAKEINSTALLATIONTOKEN0123456789abcdefABCD"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


class Fake:
    def __init__(self, expires=None, comment_status=201):
        self.calls = []
        self.expires = expires or (NOW + timedelta(hours=1))
        self.comment_status = comment_status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body))
        if url.endswith("/access_tokens"):
            payload = {
                "token": FAKE_BEARER,
                "expires_at": self.expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            return 201, json.dumps(payload).encode()
        if self.comment_status == 201:
            return 201, json.dumps({"id": 77, "html_url": "https://x/c/77"}).encode()
        return self.comment_status, b'{"message": "nope"}'


def make(pem, fake, clock=None, repos=("acme/widgets",)):
    now = {"t": NOW}
    app = GitHubApp(
        app_id="123",
        installation_id="456",
        private_key=pem,
        repos=repos,
        transport=fake,
        clock=clock or (lambda: now["t"]),
    )
    return app, now


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def test_comment_uses_installation_token(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    out = app.post_comment("acme/widgets", 5, "hello")
    assert out == {"comment_id": 77, "url": "https://x/c/77"}
    exch, comment = fake.calls
    assert exch[0] == "POST"
    assert exch[1] == "https://api.github.com/app/installations/456/access_tokens"
    assert exch[2]["Authorization"].startswith("Bearer ")
    assert comment[1] == "https://api.github.com/repos/acme/widgets/issues/5/comments"
    assert comment[2]["Authorization"] == f"Bearer {FAKE_BEARER}"
    assert comment[2]["Accept"] == "application/vnd.github+json"
    assert "X-GitHub-Api-Version" in comment[2]
    assert json.loads(comment[3]) == {"body": "hello"}


def test_a_comment_is_edited_in_place(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    out = app.update_issue_comment("acme/widgets", 77, "edited")
    assert out == {"comment_id": 77, "url": "https://x/c/77"}
    _exch, edit = fake.calls
    assert edit[0] == "PATCH"
    assert edit[1] == "https://api.github.com/repos/acme/widgets/issues/comments/77"
    assert edit[2]["Authorization"] == f"Bearer {FAKE_BEARER}"
    assert json.loads(edit[3]) == {"body": "edited"}


def test_editing_a_deleted_comment_is_a_404(pem):
    app, _ = make(pem, Fake(comment_status=404))
    with pytest.raises(GitHubError) as exc:
        app.update_issue_comment("acme/widgets", 77, "edited")
    assert exc.value.code == "http_404"


@pytest.mark.parametrize("comment_id", [0, -1, True, "77"])
def test_a_comment_edit_needs_a_comment_id(pem, comment_id):
    fake = Fake()
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as exc:
        app.update_issue_comment("acme/widgets", comment_id, "x")
    assert exc.value.code == "bad_input"
    assert fake.calls == []


def test_a_comment_edit_outside_the_allowlist_makes_no_call(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as exc:
        app.update_issue_comment("evil/repo", 77, "x")
    assert exc.value.code == "repo_not_allowed"
    assert fake.calls == []


def test_jwt_is_rs256_with_claims(pem, key):
    fake = Fake()
    app, _ = make(pem, fake)
    app.post_comment("acme/widgets", 1, "x")
    jwt = fake.calls[0][2]["Authorization"].split(" ", 1)[1]
    h, p, s = jwt.split(".")
    assert "=" not in jwt
    assert json.loads(_b64d(h)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_b64d(p))
    ts = int(NOW.timestamp())
    assert claims == {"iat": ts - 60, "exp": ts + 540, "iss": "123"}
    key.public_key().verify(_b64d(s), f"{h}.{p}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_token_cached_until_five_minutes_before_expiry(pem):
    fake = Fake(expires=NOW + timedelta(hours=1))
    app, now = make(pem, fake)
    app.post_comment("acme/widgets", 1, "a")
    now["t"] = NOW + timedelta(minutes=54)
    app.post_comment("acme/widgets", 1, "b")
    assert sum(c[1].endswith("/access_tokens") for c in fake.calls) == 1
    now["t"] = NOW + timedelta(minutes=55, seconds=1)
    app.post_comment("acme/widgets", 1, "c")
    assert sum(c[1].endswith("/access_tokens") for c in fake.calls) == 2


def test_not_allowlisted_repo_makes_no_network_call(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as exc:
        app.post_comment("evil/repo", 1, "x")
    assert exc.value.code == "repo_not_allowed"
    assert not exc.value.retryable
    assert fake.calls == []


@pytest.mark.parametrize("repo", ["acme/widgets/../x", "acme", "ACME/Widgets/extra", "../../x"])
def test_malformed_repo_rejected(pem, repo):
    fake = Fake()
    app, _ = make(pem, fake, repos=("acme/widgets",))
    with pytest.raises(GitHubError):
        app.post_comment(repo, 1, "x")
    assert fake.calls == []


@pytest.mark.parametrize("status,retryable", [(500, True), (503, True), (403, False), (404, False)])
def test_status_classification(pem, status, retryable):
    app, _ = make(pem, Fake(comment_status=status))
    with pytest.raises(GitHubError) as exc:
        app.post_comment("acme/widgets", 1, "x")
    assert exc.value.retryable is retryable


def test_network_error_is_retryable(pem):
    def boom(*a):
        raise OSError("down")

    app, _ = make(pem, boom)
    with pytest.raises(GitHubError) as exc:
        app.post_comment("acme/widgets", 1, "x")
    assert exc.value.retryable


def test_logs_never_contain_token_or_key(pem, caplog):
    caplog.set_level(logging.DEBUG)
    app, _ = make(pem, Fake(comment_status=500))
    with pytest.raises(GitHubError) as exc:
        app.post_comment("acme/widgets", 1, "x")
    with pytest.raises(GitHubError):
        app.post_comment("other/x", 1, "x")
    text = caplog.text + str(exc.value)
    assert FAKE_BEARER not in text
    assert "PRIVATE KEY" not in text
    assert pem.splitlines()[1] not in text


def test_api_base_configurable(pem):
    fake = Fake()
    app = GitHubApp(
        app_id="1",
        installation_id="2",
        private_key=pem,
        repos=["a/b"],
        transport=fake,
        api_base="http://localhost:9/",
        clock=lambda: NOW,
    )
    app.post_comment("a/b", 3, "x")
    assert fake.calls[0][1] == "http://localhost:9/app/installations/2/access_tokens"


class Revoking(Fake):
    """The first ``n`` comment calls answer 401, as after a server-side token revocation."""

    def __init__(self, n):
        super().__init__()
        self.left = n

    def __call__(self, method, url, headers, body, timeout):
        if not url.endswith("/access_tokens") and self.left:
            self.left -= 1
            self.calls.append((method, url, dict(headers), body))
            return 401, b'{"message": "Bad credentials"}'
        return super().__call__(method, url, headers, body, timeout)


def test_a_revoked_cached_token_is_re_exchanged_once(pem):
    fake = Revoking(1)
    app, _ = make(pem, fake)
    app.installation_token()  # cached, then revoked server-side
    assert app.post_comment("acme/widgets", 5, "hello")["comment_id"] == 77
    assert sum(c[1].endswith("/access_tokens") for c in fake.calls) == 2


def test_a_401_with_a_fresh_token_is_not_retried_again(pem):
    fake = Revoking(5)
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as err:
        app.post_comment("acme/widgets", 5, "hello")
    assert err.value.code == "http_401"
    assert not err.value.retryable
    assert sum(not c[1].endswith("/access_tokens") for c in fake.calls) == 2


def test_push_token_is_fresh_single_repo_contents_write(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    assert app.push_token("acme/widgets") == FAKE_BEARER
    assert app.push_token("acme/widgets") == FAKE_BEARER
    mints = [c for c in fake.calls if c[1].endswith("/access_tokens")]
    assert len(mints) == 2  # never cached
    for _, _, headers, body in mints:
        assert json.loads(body) == {
            "repositories": ["widgets"],
            "permissions": {"contents": "write"},
        }
        assert headers["Authorization"] != f"Bearer {FAKE_BEARER}"  # signed with the App JWT
    app.installation_token()  # the general token is a separate, unscoped exchange
    assert fake.calls[-1][3] is None


def test_push_token_refuses_off_allowlist_without_network(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as err:
        app.push_token("evil/repo")
    assert err.value.code == "repo_not_allowed"
    assert fake.calls == []


def test_push_token_scope_mismatch_is_refused(pem):
    def fake(method, url, headers, body, timeout):
        payload = {
            "token": FAKE_BEARER,
            "expires_at": "2030-01-01T00:00:00Z",
            "repositories": [{"full_name": "acme/widgets"}, {"full_name": "acme/other"}],
        }
        return 201, json.dumps(payload).encode()

    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as err:
        app.push_token("acme/widgets")
    assert err.value.code == "token_scope_mismatch"


def test_app_has_no_merge_call():
    assert not [n for n in dir(GitHubApp) if "merge" in n.lower()]


def test_deadline_bounds_http_timeouts_and_refuses_when_past(pem):
    seen = []

    def fake(method, url, headers, body, timeout):
        seen.append(timeout)
        return Fake()(method, url, headers, body, timeout)

    app, now = make(pem, fake)
    with app.deadline(NOW + timedelta(seconds=4)):
        app.post_comment("acme/widgets", 1, "x")
    assert seen
    assert max(seen) <= 4
    seen.clear()
    with app.deadline(NOW - timedelta(seconds=1)):
        with pytest.raises(GitHubError) as err:
            app.post_comment("acme/widgets", 1, "x")
    assert err.value.code == "deadline_exceeded"
    assert err.value.retryable
    assert seen == []
    app.post_comment("acme/widgets", 1, "x")  # outside the block: the default bound again
    assert seen == [15]


def test_list_check_suites_paginates_and_trims(pem):
    def page(n):
        return [
            {"app": {"slug": f"app{n}-{i}"}, "status": "completed", "conclusion": "success"}
            for i in range(100 if n == 1 else 2)
        ]

    class SuitesFake(Fake):
        def __call__(self, method, url, headers, body, timeout):
            if "/check-suites" not in url:
                return super().__call__(method, url, headers, body, timeout)
            self.calls.append((method, url, dict(headers), body))
            n = int(url.rsplit("page=", 1)[1])
            return 200, json.dumps({"check_suites": page(n)}).encode()

    fake = SuitesFake()
    app, _ = make(pem, fake)
    out = app.list_check_suites("acme/widgets", "ab12" * 10)
    assert len(out) == 102
    assert out[0] == {
        "app_slug": "app1-0",
        "status": "completed",
        "conclusion": "success",
    }
    with pytest.raises(GitHubError) as err:
        app.list_check_suites("acme/widgets", "../x")
    assert err.value.code == "bad_input"
    with pytest.raises(GitHubError) as err:
        app.list_check_suites("other/repo", "ab12" * 10)
    assert err.value.code == "repo_not_allowed"


def test_list_check_runs_paginates_and_keeps_only_the_report_fields(pem):
    def page(n):
        return [
            {
                "id": i,
                "name": f"check {n}-{i}",
                "app": {"slug": "gitguardian", "owner": {"login": "x"}},
                "status": "completed",
                "conclusion": "failure",
                "html_url": "https://github.com/acme/widgets/runs/1",
                "output": {"title": "1 secret uncovered!", "text": "| t |", "summary": "s"},
                "head_sha": "ab12" * 10,
            }
            for i in range(100 if n == 1 else 1)
        ]

    class RunsFake(Fake):
        def __call__(self, method, url, headers, body, timeout):
            if "/check-runs" not in url:
                return super().__call__(method, url, headers, body, timeout)
            self.calls.append((method, url, dict(headers), body))
            n = int(url.rsplit("page=", 1)[1])
            return 200, json.dumps({"check_runs": page(n)}).encode()

    fake = RunsFake()
    app, _ = make(pem, fake)
    out = app.list_check_runs("acme/widgets", "ab12" * 10)
    assert len(out) == 101
    assert out[0] == {
        "name": "check 1-0",
        "app_slug": "gitguardian",
        "status": "completed",
        "conclusion": "failure",
        "title": "1 secret uncovered!",
        "text": "| t |",
        "html_url": "https://github.com/acme/widgets/runs/1",
    }
    urls = [c[1] for c in fake.calls if "/check-runs" in c[1]]
    assert "filter=latest" in urls[0]
    assert all(c[0] == "GET" for c in fake.calls if "/check-runs" in c[1])


def test_list_check_runs_refuses_a_bad_sha_or_an_unlisted_repo_before_any_call(pem):
    fake = Fake()
    app, _ = make(pem, fake)
    with pytest.raises(GitHubError) as err:
        app.list_check_runs("acme/widgets", "../x")
    assert err.value.code == "bad_input"
    with pytest.raises(GitHubError) as err:
        app.list_check_runs("other/repo", "ab12" * 10)
    assert err.value.code == "repo_not_allowed"
    assert fake.calls == []


def test_pr_facts_shape():
    from culture_rules.apps.github import PR_FACT_FIELDS, complete_pr_facts, pr_facts

    pr = {
        "draft": True,
        "head": {"sha": "a" * 40, "ref": "feat", "repo": {"full_name": "fork/r"}},
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "o/r"}},
        "user": {"login": "alice"},
    }
    facts = {
        "head_sha": "a" * 40,
        "head_branch": "feat",
        "head_repo": "fork/r",
        "base_repo": "o/r",
        "base_branch": "main",
        "base_sha": "b" * 40,
        "draft": True,
        "pr_author": "alice",
    }
    assert pr_facts(pr) == facts
    assert tuple(pr_facts(pr)) == PR_FACT_FIELDS
    assert complete_pr_facts(pr) == facts
    assert pr_facts(None) == {}
    assert pr_facts(["x"]) == {}


def test_pr_facts_omit_missing_and_malformed_fields_never_null():
    from culture_rules.apps.github import complete_pr_facts, pr_facts

    assert pr_facts({}) == {}  # no null repos to compare equal, no default draft
    assert complete_pr_facts({}) is None
    assert complete_pr_facts(None) is None
    bad = {
        "draft": "false",
        "head": {"sha": "abc123", "ref": "", "repo": None},
        "base": {"sha": "g" * 40, "ref": 7, "repo": {"full_name": "not a repo"}},
        "user": {"login": ""},
    }
    assert pr_facts(bad) == {}
    assert complete_pr_facts(bad) is None
    # a deleted fork: only head_repo is gone, so head_repo == base_repo cannot hold
    fork = {
        "draft": False,
        "head": {"sha": "a" * 40, "ref": "feat", "repo": None},
        "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "o/r"}},
        "user": {"login": "alice"},
    }
    facts = pr_facts(fork)
    assert "head_repo" not in facts
    assert facts["base_repo"] == "o/r"
    assert facts["draft"] is False
    assert complete_pr_facts(fork) is None
    assert pr_facts({"draft": 0}) == {}  # a real bool only


class Pages(Fake):
    """Answers the comment listing with ``pages`` (lists of raw comments)."""

    def __init__(self, pages):
        super().__init__()
        self.pages = list(pages)

    def __call__(self, method, url, headers, body, timeout):
        if url.endswith("/access_tokens"):
            return super().__call__(method, url, headers, body, timeout)
        self.calls.append((method, url, dict(headers), body))
        return 200, json.dumps(self.pages.pop(0) if self.pages else []).encode()


def test_the_comments_of_a_pr_are_listed_with_their_app(pem):
    full = [{"id": n, "html_url": f"u{n}", "body": "b", "user": {}} for n in range(100)]
    last = [
        {"id": 500, "html_url": "u500", "body": "mine", "performed_via_github_app": {"id": 123}},
        {"id": 501, "body": None, "performed_via_github_app": {"id": True}},
    ]
    fake = Pages([full, last])
    app, _ = make(pem, fake)
    out = app.list_issue_comments("acme/widgets", 7)
    assert len(out) == 102
    assert out[100] == {"comment_id": 500, "url": "u500", "body": "mine", "app_id": "123"}
    assert out[101]["app_id"] is None
    assert out[101]["body"] == ""
    urls = [c[1] for c in fake.calls if "/comments" in c[1]]
    assert urls[0].endswith("/repos/acme/widgets/issues/7/comments?per_page=100&page=1")
    assert urls[1].endswith("page=2")
    assert app.app_id == "123"


def test_a_request_guard_sees_every_request_and_can_stop_one(pem):
    # d26: the status board counts each HTTP request (token exchange included)
    fake = Fake()
    app, _ = make(pem, fake)
    seen = []

    def guard():
        seen.append(1)
        if len(seen) > 2:
            raise GitHubError("budget_exhausted", retryable=True)

    with app.request_guard(guard):
        app.post_comment("acme/widgets", 1, "a")  # token exchange + post
        with pytest.raises(GitHubError) as exc:
            app.post_comment("acme/widgets", 1, "b")
    assert exc.value.code == "budget_exhausted"
    assert len(fake.calls) == 2  # the third request was never sent
    app.post_comment("acme/widgets", 1, "c")  # outside the block: unguarded
    assert len(seen) == 3


def test_a_trickling_response_is_cut_by_the_whole_call_deadline():
    # Codex round 3: the socket timeout bounds each read, not the call; one byte every
    # 0.5 s would never time out. The transport reads within one monotonic deadline.
    import threading
    import time
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from culture_rules.apps.github import urllib_transport

    class Trickle(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", "100")
            self.end_headers()
            try:
                for _ in range(100):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.5)
            except OSError:
                pass

    server = HTTPServer(("127.0.0.1", 0), Trickle)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    started = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            urllib_transport("GET", url, {}, None, 1.5)
    finally:
        server.shutdown()
    assert time.monotonic() - started < 4


def test_a_response_body_larger_than_the_cap_is_refused():
    import io

    from culture_rules.apps.github import _read_within

    big, never = io.BytesIO(b"y" * 100), float("inf")
    with pytest.raises(ValueError):
        _read_within(big, deadline_at=never, max_bytes=10)
    assert _read_within(io.BytesIO(b"ok"), deadline_at=never, max_bytes=10) == b"ok"


@pytest.mark.parametrize("drip", ["headers", "chunks", "trailers"])
def test_a_dripping_response_returns_within_the_hard_deadline(pem, drip):
    # Codex round 4: http.client reads headers, chunk sizes and trailers line by line
    # under the original socket timeout; a watchdog bounds the whole call
    import socket
    import threading
    import time

    from culture_rules.apps.github import urllib_transport

    head = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nX-Pad: " + b"p" * 40 + b"\r\n\r\n"
    body = b"2\r\n{}\r\n" * 20 + b"0\r\n"
    trailers = b"X-Trailer: " + b"t" * 40 + b"\r\n\r\n"

    def serve(listener):
        conn, _ = listener.accept()
        conn.recv(65536)
        if drip == "headers":
            parts = [bytes([b]) for b in head] + [body + b"\r\n"]
        elif drip == "chunks":
            parts = [head] + [bytes([b]) for b in body] + [b"\r\n"]
        else:
            parts = [head, body] + [bytes([b]) for b in trailers]
        try:
            for part in parts:
                conn.sendall(part)
                time.sleep(0.2)
        except OSError:
            pass
        conn.close()

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    threading.Thread(target=serve, args=(listener,), daemon=True).start()
    url = f"http://127.0.0.1:{listener.getsockname()[1]}/"

    token = {"token": FAKE_BEARER, "expires_at": "2099-01-01T00:00:00Z"}

    def transport(method, u, headers, data, timeout):
        if u.endswith("/access_tokens"):
            return 201, json.dumps(token).encode()
        return urllib_transport("GET", url, headers, None, timeout)

    app = GitHubApp(
        app_id="1",
        installation_id="2",
        private_key=pem,
        repos=("acme/widgets",),
        transport=transport,
    )
    started = time.monotonic()
    with app.deadline(datetime.now(UTC) + timedelta(seconds=1.0)), app.watchdog():
        with pytest.raises(GitHubError) as exc:
            app.get_pull("acme/widgets", 1)
    took = time.monotonic() - started
    listener.close()
    assert exc.value.code == "deadline_exceeded"
    assert took < 1.6


def test_outside_the_status_stage_a_request_runs_inline(pem):
    # the watchdog is opt-in: push, settle, threads, plain comments and the API's calls
    # run their requests on the calling thread, exactly as before d26
    import threading

    from culture_rules.apps import github as gh

    threads = []

    def transport(method, url, headers, body, timeout):
        threads.append(threading.current_thread())
        return Fake()(method, url, headers, body, timeout)

    app, _ = make(pem, transport)
    taken = 0
    while gh._WATCH_SLOTS.acquire(blocking=False):  # the watchdog pool exhausted
        taken += 1
    try:
        assert app.post_comment("acme/widgets", 1, "x")["comment_id"] == 77
        with app.watchdog(), pytest.raises(GitHubError) as exc:
            app.post_comment("acme/widgets", 1, "y")
    finally:
        for _ in range(taken):
            gh._WATCH_SLOTS.release()
    assert threads == [threading.current_thread()] * 2  # token exchange and post: inline
    assert exc.value.code == "transport_busy"  # only inside the status stage's context
