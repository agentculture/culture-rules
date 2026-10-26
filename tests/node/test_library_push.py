"""library.push action port: multipart upload to a library, then queue the item when the actor
has a queue_path (#53 t5, culture-rules#86)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import library as library_mod
from culture_rules.node.actions.library import LibraryPushPort, encode_multipart
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

NOW = datetime(2026, 10, 11, 3, 30, tzinfo=UTC)
DEADLINE = NOW + timedelta(minutes=5)
BASE = "http://localhost:8780"
ACTOR_ID = "xteink"
KEY_REF = "grant:XTEINK_NEWSPAPER"
KEY_VALUE = "xtk_fake_value_for_tests"
TITLE = "Daily newspaper 2026-10-11"
EPUB = b"PK\x03\x04fake-epub-bytes"
UPLOAD_URL = BASE + "/api/library"
QUEUE_URL = BASE + "/api/devices/3/queue"
POST = "POST"
UPLOAD_OK = (201, {"item": {"id": 7, "title": TITLE}, "created": True})
QUEUE_OK = (201, {"id": 11, "device_id": 3, "item_id": 7, "state": "queued"})
CONTENT_TYPE = "Content-Type"
INVALID_REQUEST = "invalid_request"
INVALID_ACTOR = "invalid_actor"
BAD_RESPONSE = "bad_response"
TOO_LARGE = "too_large"
FILE = "file"
TITLE_FIELD = "title"
AUTHOR = "author"
LITERAL_KEY = "xtk_literal_key"


@dataclass
class Call:
    method: str
    url: str
    headers: dict
    body: bytes
    timeout: float


def _b64(data: bytes = EPUB) -> str:
    return base64.b64encode(data).decode("ascii")


class FakeTransport:
    """Scripted responses, one per call; an exception instance is raised instead."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append(Call(method, url, dict(headers), body, timeout))
        reply = self.responses.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        status, payload = reply
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, raw


KINDS = ["book", "article", "transcript", "newspaper"]
QUEUE_PATH = "/api/devices/{device_id}/queue"


def _params(**over):
    params = {
        "base_url": BASE,
        "api_key": KEY_REF,
        "device_id": 3,
        "upload_path": "/api/library",
        "queue_path": QUEUE_PATH,
        "kinds": KINDS,
        "visibilities": ["private", "public"],
    }
    params.update(over)
    return {k: v for k, v in params.items() if v is not _DROP}


_DROP = object()


def _store(params=None, *, kind="service", enabled=True):
    store = MemoryStore()
    actor = {"id": ACTOR_ID, "name": ACTOR_ID, "kind": kind, "enabled": enabled}
    actor["params"] = _params() if params is None else params
    store.put(ACTORS_COLLECTION, actor)
    return store


def _port(store, transport, secrets=None):
    resolved = []

    def secret_runner(name):
        resolved.append(name)
        if secrets is not None:
            return secrets(name)
        return KEY_VALUE

    port = LibraryPushPort(
        store, transport=transport, secret_runner=secret_runner, clock=lambda: NOW
    )
    return port, resolved


def _ctx():
    return InvocationContext(run_id="run", step_id="push", kind="action", host="spark")


def _input(**over):
    data = {"actor": ACTOR_ID, "content_base64": _b64(), "title": TITLE, "kind": "article"}
    data.update(over)
    return data


def _invoke(port, key="k1", **over):
    return port.invoke(_input(**over), key, DEADLINE, context=_ctx())


def _parts(call):
    """Parse the multipart body of a recorded call into {name: (headers, value)}."""
    content_type = call.headers[CONTENT_TYPE]
    boundary = content_type.split("boundary=", 1)[1].encode()
    out = {}
    for chunk in call.body.split(b"--" + boundary)[1:-1]:
        head, _, value = chunk[2:-2].partition(b"\r\n\r\n")
        name = head.split(b'name="', 1)[1].split(b'"', 1)[0].decode()
        out[name] = (head.decode(), value)
    return out


# ------------------------------------------------------------------ the happy path


def test_push_uploads_then_queues_and_returns_ids():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.outcome == COMPLETED
    assert dict(result.output) == {"item_id": 7, "created": True, "queue_id": 11}


def test_upload_is_a_multipart_post_to_the_library():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    _invoke(port)
    upload = transport.calls[0]
    assert upload.method == POST
    assert upload.url == UPLOAD_URL
    assert upload.headers[CONTENT_TYPE].startswith("multipart/form-data; boundary=")


def test_upload_carries_file_title_author_and_kind():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    _invoke(port)
    parts = _parts(transport.calls[0])
    assert parts[FILE][1] == EPUB
    assert 'filename="daily-newspaper-2026-10-11.epub"' in parts[FILE][0]
    assert "Content-Type: application/epub+zip" in parts[FILE][0]
    assert parts[TITLE_FIELD][1] == TITLE.encode()
    assert parts[AUTHOR][1] == b"culture-rules"
    assert parts["kind"][1] == b"article"
    assert "visibility" not in parts


@pytest.mark.parametrize("kind", KINDS)
def test_kind_param_is_sent_as_given(kind):
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    assert _invoke(port, kind=kind).outcome == COMPLETED
    assert _parts(transport.calls[0])["kind"][1] == kind.encode()


def test_author_param_overrides_the_default():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    _invoke(port, author="The desk")
    assert _parts(transport.calls[0])[AUTHOR][1] == b"The desk"


def test_both_calls_carry_the_bearer_from_the_grant_ref():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, resolved = _port(_store(), transport)
    _invoke(port)
    assert resolved == ["XTEINK_NEWSPAPER"]
    for call in transport.calls:
        assert call.headers["Authorization"] == "Bearer " + KEY_VALUE


def test_queue_posts_the_returned_item_id_as_json():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    _invoke(port)
    queue = transport.calls[1]
    assert queue.method == POST
    assert queue.url == QUEUE_URL
    assert queue.headers[CONTENT_TYPE] == "application/json"
    assert json.loads(queue.body) == {"item_id": 7}


def test_a_deduplicated_upload_still_queues_and_reports_created_false():
    transport = FakeTransport((200, {"item": {"id": 7}, "created": False}), QUEUE_OK)
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.output["created"] is False
    assert len(transport.calls) == 2


def test_device_id_param_overrides_the_actors():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    _invoke(port, device_id="12")
    assert transport.calls[1].url == BASE + "/api/devices/12/queue"


def test_base_url_trailing_slash_and_path_prefix_are_kept():
    store = _store(_params(base_url=BASE + "/xt/"))
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(store, transport)
    _invoke(port)
    assert transport.calls[0].url == BASE + "/xt/api/library"


def test_timeout_comes_from_the_deadline():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    _invoke(port)
    assert transport.calls[0].timeout == pytest.approx(120.0)


def test_actor_falls_back_to_context_actor():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    data = _input()
    del data["actor"]
    ctx = InvocationContext(run_id="r", step_id="s", kind="action", host="h", actor=ACTOR_ID)
    result = port.invoke(data, "k", DEADLINE, context=ctx)
    assert result.outcome == COMPLETED


def test_a_completed_push_replays_for_the_same_key():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    first = _invoke(port)
    second = _invoke(port)
    assert second == first
    assert len(transport.calls) == 2


def test_port_declares_idempotency_support():
    assert LibraryPushPort.supports_idempotency_key is True


# ------------------------------------------------------------------ a refused upload


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (413, TOO_LARGE, False),
        (415, "unsupported_format", False),
        (422, "bad_magic", False),
        (401, "unauthorized", False),
        (503, "converter_missing", True),
        (500, "boom", True),
        (429, "slow_down", True),
    ],
)
def test_a_refused_upload_queues_nothing(status, code, retryable):
    transport = FakeTransport((status, {"detail": "no", "code": code}))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.outcome == FAILED
    assert result.retryable is retryable
    assert code in result.error
    assert len(transport.calls) == 1


def test_an_upload_error_never_echoes_the_detail_text():
    transport = FakeTransport((422, {"detail": "secret-ish detail", "code": "bad_magic"}))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert "secret-ish" not in result.error


def test_a_non_code_error_field_is_not_echoed():
    transport = FakeTransport((422, {"detail": "x", "code": "Bearer xtk_abc def"}))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert "xtk_abc" not in result.error


def test_a_network_error_on_upload_is_retryable_and_queues_nothing():
    transport = FakeTransport(ConnectionRefusedError("refused"))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.retryable is True
    assert result.error.startswith("network_error")
    assert len(transport.calls) == 1


def test_an_upload_reply_without_an_item_id_queues_nothing():
    transport = FakeTransport((201, {"created": True}))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.error.startswith(BAD_RESPONSE)
    assert len(transport.calls) == 1


def test_a_non_json_upload_reply_is_a_bad_response():
    transport = FakeTransport((201, b"<html>"))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.error.startswith(BAD_RESPONSE)


def test_a_redirect_is_refused():
    transport = FakeTransport((302, b""))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.error.startswith("upload_failed")
    assert result.retryable is False


# ------------------------------------------------------------------ the queue call


def test_an_unknown_device_fails_the_queue_without_retry():
    transport = FakeTransport(UPLOAD_OK, (404, {"detail": "Not Found"}))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.retryable is False
    assert result.error.startswith("queue_failed")
    assert "item 7" in result.error


def test_a_queue_server_error_is_retryable():
    transport = FakeTransport(UPLOAD_OK, (502, b"bad gateway"))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.retryable is True


def test_a_queue_network_error_is_retryable():
    transport = FakeTransport(UPLOAD_OK, TimeoutError("slow"))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.retryable is True


def test_a_queue_reply_without_an_id_is_a_bad_response():
    transport = FakeTransport(UPLOAD_OK, (201, {"state": "queued"}))
    port, _ = _port(_store(), transport)
    result = _invoke(port)
    assert result.error.startswith(BAD_RESPONSE)


def test_a_failed_push_is_not_replayed():
    transport = FakeTransport((503, {"code": "converter_missing"}), UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    _invoke(port)
    result = _invoke(port)
    assert result.outcome == COMPLETED


# ------------------------------------------------------------------ refused before any call


def _refused(store=None, secrets=None, **over):
    transport = FakeTransport()
    port, _ = _port(store or _store(), transport, secrets=secrets)
    result = _invoke(port, **over)
    assert transport.calls == []
    assert result.outcome == FAILED
    return result


@pytest.mark.parametrize("content", ["not base64!!", "", None, 12, "QUJD="])
def test_invalid_content_is_refused(content):
    result = _refused(content_base64=content)
    assert result.retryable is False
    assert result.error.startswith(INVALID_REQUEST)


def test_oversized_content_is_refused(monkeypatch):
    monkeypatch.setattr(library_mod, "MAX_BYTES", 8)
    result = _refused(content_base64=_b64(b"123456789"))
    assert result.error.startswith(TOO_LARGE)
    assert result.retryable is False


def test_oversized_encoded_text_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(library_mod, "MAX_BYTES", 3)
    result = _refused(content_base64="A" * 400)
    assert result.error.startswith(TOO_LARGE)


@pytest.mark.parametrize("title", ["", None, 5, "   "])
def test_a_missing_title_is_refused(title):
    result = _refused(title=title)
    assert result.error.startswith(INVALID_REQUEST)


@pytest.mark.parametrize("kind", ["magazine", "", "Book", " article", 5, ["book"], True, None])
def test_an_unknown_kind_is_refused_before_any_request(kind):
    result = _refused(kind=kind)
    assert result.retryable is False
    assert result.error.startswith(INVALID_REQUEST)


def test_a_non_string_author_is_refused():
    result = _refused(author=["a"])
    assert result.error.startswith(INVALID_REQUEST)


@pytest.mark.parametrize("device", ["3/../keys", "-1", "abc", 0, True, 2.5, ""])
def test_a_bad_device_id_is_refused(device):
    result = _refused(device_id=device)
    assert result.error.startswith(INVALID_REQUEST)


def test_no_device_anywhere_is_refused():
    store = _store(_params(device_id=_DROP))
    result = _refused(store)
    assert result.error.startswith("device_missing")


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://localhost/x",
        "file:///etc/passwd",
        "localhost:8780",
        "http://user:pw@localhost:8780",
        "http://localhost:8780/?q=1",
        "http:///nohost",
        "",
        None,
    ],
)
def test_a_bad_base_url_is_refused(base_url):
    store = _store(_params(base_url=base_url))
    result = _refused(store)
    assert result.error.startswith(INVALID_ACTOR)
    assert result.retryable is False


@pytest.mark.parametrize("api_key", [LITERAL_KEY, "", None, "grant:"])
def test_the_key_must_be_a_grant_reference(api_key):
    store = _store(_params(api_key=api_key))
    result = _refused(store)
    assert result.error.startswith(INVALID_ACTOR)


def test_a_literal_key_is_never_echoed():
    store = _store(_params(api_key=LITERAL_KEY))
    result = _refused(store)
    assert LITERAL_KEY not in result.error


def test_an_unresolvable_key_fails_without_echoing_it():
    def broken(_name):
        raise RuntimeError("grant is down")

    result = _refused(secrets=broken)
    assert result.error.startswith("secret_unresolved")
    assert "grant is down" not in result.error


def test_a_non_service_actor_is_refused():
    result = _refused(_store(kind="agent"))
    assert result.error.startswith("actor_kind_mismatch")


def test_a_disabled_actor_is_refused():
    result = _refused(_store(enabled=False))
    assert result.error.startswith("actor_disabled")


def test_an_unknown_actor_is_refused():
    result = _refused(actor="nobody")
    assert result.error.startswith("actor_not_found")


def test_a_passed_deadline_is_retryable():
    transport = FakeTransport()
    port, _ = _port(_store(), transport)
    result = port.invoke(_input(), "k", NOW - timedelta(seconds=1), context=_ctx())
    assert result.error.startswith("deadline_passed")
    assert result.retryable is True


# ------------------------------------------------------------------ the multipart encoder


def test_encode_multipart_round_trips_fields_and_a_file():
    content_type, body = encode_multipart(
        [(TITLE_FIELD, "T\r\nwith lines")], [(FILE, "a.epub", "application/epub+zip", EPUB)]
    )
    parts = _parts(Call(POST, "", {CONTENT_TYPE: content_type}, body, 0.0))
    assert parts[TITLE_FIELD][1] == b"T\r\nwith lines"
    assert parts[FILE][1] == EPUB


def test_encode_multipart_uses_a_fresh_random_boundary():
    first, _ = encode_multipart([("a", "b")], [])
    second, _ = encode_multipart([("a", "b")], [])
    assert first != second


def test_encode_multipart_picks_a_boundary_absent_from_the_content(monkeypatch):
    tokens = iter(["clash", "clean"])
    monkeypatch.setattr(library_mod.secrets, "token_hex", lambda _n: next(tokens))
    content_type, _ = encode_multipart([("a", "x--culture-rules-clash")], [])
    assert content_type.endswith("culture-rules-clean")


@pytest.mark.parametrize("name", ['bad"name', "line\r\nbreak"])
def test_encode_multipart_refuses_unsafe_header_text(name):
    with pytest.raises(ValueError):
        encode_multipart([(name, "v")], [])


@pytest.mark.parametrize(
    ("title", "filename"),
    [
        (TITLE, "daily-newspaper-2026-10-11.epub"),
        ('  Ünïcode / slashes \\ and "quotes" ', "n-code-slashes-and-quotes.epub"),
        ("***", "upload.epub"),
    ],
)
def test_filename_is_a_safe_slug_of_the_title(title, filename):
    assert library_mod.epub_filename(title) == filename


# ------------------------------------------------------------------ the default transport


def test_default_transport_returns_status_and_body_on_http_error(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise library_mod.urllib.error.HTTPError(
                request.full_url, 404, "nf", {}, _Body(b'{"detail":"x"}')
            )

    monkeypatch.setattr(library_mod, "_opener", Opener)
    status, raw = library_mod.urllib_transport(POST, UPLOAD_URL, {}, b"", 5.0)
    assert status == 404
    assert raw == b'{"detail":"x"}'


def test_default_transport_returns_a_2xx_reply(monkeypatch):
    seen = {}

    class Opener:
        def open(self, request, timeout):
            seen["method"] = request.get_method()
            seen["timeout"] = timeout
            return _Body(b"{}", status=201)

    monkeypatch.setattr(library_mod, "_opener", Opener)
    status, _ = library_mod.urllib_transport(POST, UPLOAD_URL, {"A": "b"}, b"x", 5.0)
    assert status == 201
    assert seen == {"method": POST, "timeout": 5.0}


def test_default_transport_maps_a_redirect_to_its_status(monkeypatch):
    class Opener:
        def open(self, request, timeout):
            raise library_mod.RedirectRefused(301)

    monkeypatch.setattr(library_mod, "_opener", Opener)
    status, _ = library_mod.urllib_transport(POST, UPLOAD_URL, {}, b"", 5.0)
    assert status == 301


def test_default_opener_has_no_proxy_or_redirect_following():
    opener = library_mod._opener()
    names = {type(h).__name__ for h in opener.handlers}
    assert "ProxyHandler" not in names
    assert "HTTPRedirectHandler" not in names


class _Body:
    def __init__(self, data, status=200):
        self._data = data
        self.status = status

    def read(self, n=-1):
        return self._data if n < 0 else self._data[:n]

    def close(self):
        pass


# ------------------------------------------------------------------ the queue's own timeout


class SlowUpload(FakeTransport):
    """The upload takes ``upload_s`` seconds of the ``clock``'s time (PR #54 review)."""

    def __init__(self, clock, upload_s, *responses):
        super().__init__(*responses)
        self.clock, self.upload_s = clock, upload_s

    def __call__(self, method, url, headers, body, timeout):
        reply = super().__call__(method, url, headers, body, timeout)
        if url == UPLOAD_URL:
            self.clock.now += timedelta(seconds=self.upload_s)
        return reply


class MovingClock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now


def _slow_push(upload_s):
    clock = MovingClock()
    transport = SlowUpload(clock, upload_s, UPLOAD_OK, QUEUE_OK)
    port = LibraryPushPort(
        _store(), transport=transport, secret_runner=lambda _name: KEY_VALUE, clock=clock
    )
    return _invoke(port), transport


def test_the_queue_timeout_is_what_remains_after_the_upload():
    _, transport = _slow_push(250)  # the deadline is 300 s away
    assert transport.calls[1].timeout == pytest.approx(50.0)


def test_an_upload_that_uses_up_the_deadline_queues_nothing():
    _, transport = _slow_push(300)
    assert len(transport.calls) == 1


def test_an_upload_that_uses_up_the_deadline_fails_retryably():
    result, _ = _slow_push(300)
    assert (result.outcome, result.retryable) == (FAILED, True)


def test_an_upload_that_uses_up_the_deadline_names_the_deadline():
    result, _ = _slow_push(300)
    assert result.error.startswith("deadline_passed")


# ------------------------------------------------------------------ a bounded replay ledger


def _pushes(monkeypatch, keys):
    monkeypatch.setattr(library_mod, "LEDGER_SIZE", 2)
    transport = FakeTransport(*[r for _ in range(len(keys) + 1) for r in (UPLOAD_OK, QUEUE_OK)])
    port, _ = _port(_store(), transport)
    for key in keys:
        _invoke(port, key=key)
    return port, transport


def test_the_push_ledger_evicts_its_oldest_push(monkeypatch):
    port, transport = _pushes(monkeypatch, ["k1", "k2", "k3"])
    _invoke(port, key="k1")
    assert len(transport.calls) == 8  # k1 pushed again: two more calls


def test_the_push_ledger_still_replays_a_recent_push(monkeypatch):
    port, transport = _pushes(monkeypatch, ["k1", "k2", "k3"])
    _invoke(port, key="k3")
    assert len(transport.calls) == 6


# ------------------------------------------------------------------ the actor describes the library


UPLOAD_ONLY = {"queue_path": _DROP, "device_id": _DROP}


def test_without_a_queue_path_the_push_only_uploads():
    transport = FakeTransport(UPLOAD_OK)
    port, _ = _port(_store(_params(**UPLOAD_ONLY)), transport)
    result = _invoke(port)
    assert result.outcome == COMPLETED
    assert dict(result.output) == {"item_id": 7, "created": True, "queue_id": None}
    assert [call.url for call in transport.calls] == [UPLOAD_URL]


@pytest.mark.parametrize("device", ["reader-1", "-1", 0])
def test_without_a_queue_path_even_a_malformed_device_id_is_ignored(device):  # #91 review
    transport = FakeTransport(UPLOAD_OK)
    port, _ = _port(_store(_params(**UPLOAD_ONLY)), transport)
    assert _invoke(port, device_id=device).outcome == COMPLETED
    assert len(transport.calls) == 1


def test_without_a_queue_path_a_malformed_actor_device_id_is_ignored():
    transport = FakeTransport(UPLOAD_OK)
    store = _store(_params(queue_path=_DROP, device_id="reader-1"))
    port, _ = _port(store, transport)
    assert _invoke(port).outcome == COMPLETED


def test_without_a_queue_path_a_device_id_is_not_needed_or_used():
    transport = FakeTransport(UPLOAD_OK)
    port, _ = _port(_store(_params(**UPLOAD_ONLY)), transport)
    assert _invoke(port, device_id="12").outcome == COMPLETED
    assert len(transport.calls) == 1


def test_the_upload_path_comes_from_the_actor():
    store = _store(_params(upload_path="/v2/items", **UPLOAD_ONLY))
    transport = FakeTransport(UPLOAD_OK)
    port, _ = _port(store, transport)
    _invoke(port)
    assert transport.calls[0].url == BASE + "/v2/items"


def test_the_queue_path_template_comes_from_the_actor():
    store = _store(_params(queue_path="/q/{device_id}/items"))
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(store, transport)
    _invoke(port)
    assert transport.calls[1].url == BASE + "/q/3/items"


@pytest.mark.parametrize(
    "path",
    [
        None,
        "",
        "api/library",
        "/api/../keys",
        "/api/./library",
        "/api//library",
        "/a?b=1",
        "/a#f",
        "/{device_id}",
        5,
    ],
)
def test_a_bad_upload_path_is_refused(path):
    result = _refused(_store(_params(upload_path=path)))
    assert result.error.startswith(INVALID_ACTOR)
    assert result.retryable is False


@pytest.mark.parametrize(
    "path",
    [
        "/api/devices/queue",
        "/a/{device_id}/{device_id}",
        "/a/x{device_id}",
        "a/{device_id}",
        "/a/../{device_id}",
        3,
    ],
)
def test_a_bad_queue_path_is_refused(path):
    result = _refused(_store(_params(queue_path=path)))
    assert result.error.startswith(INVALID_ACTOR)


@pytest.mark.parametrize("kinds", [None, [], "newspaper", ["News"], [5], ["a b"]])
def test_the_actor_must_list_its_kinds(kinds):
    result = _refused(_store(_params(kinds=kinds)))
    assert result.error.startswith(INVALID_ACTOR)


def test_a_kind_the_actor_does_not_list_is_refused():
    result = _refused(_store(_params(kinds=["newspaper"])), kind="article")
    assert result.error == "invalid_request: kind must be one of newspaper"


@pytest.mark.parametrize("visibility", ["private", "public"])
def test_a_visibility_is_sent_as_given(visibility):
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    assert _invoke(port, visibility=visibility).outcome == COMPLETED
    assert _parts(transport.calls[0])["visibility"][1] == visibility.encode()


@pytest.mark.parametrize("visibility", ["secret", "", "Public", 1, True])
def test_a_visibility_the_actor_does_not_list_is_refused(visibility):
    result = _refused(visibility=visibility)
    assert result.error.startswith(INVALID_REQUEST)


def test_a_visibility_needs_the_actor_to_list_visibilities():
    result = _refused(_store(_params(visibilities=_DROP)), visibility="public")
    assert result.error == "invalid_request: the library actor lists no visibility values"


def test_without_visibilities_a_push_without_visibility_still_works():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(_params(visibilities=_DROP)), transport)
    assert _invoke(port).outcome == COMPLETED


@pytest.mark.parametrize("visibilities", [[], "public", ["Public"]])
def test_bad_visibilities_on_the_actor_are_refused(visibilities):
    result = _refused(_store(_params(visibilities=visibilities)))
    assert result.error.startswith(INVALID_ACTOR)


# ------------------------------------------------------------------ formats and replace (gl-t1)

REPLACE_PATH = "/api/library/{item_id}/file"
# culture-library answers a replace with the item itself, as GET /api/library/{id} does.
REPLACE_OK = (200, {"id": 7, "title": TITLE, "sha256": "ab" * 32})


def _fmt_params(**over):
    return _params(formats=["epub", "md", "txt"], **over)


def test_format_md_sends_a_md_filename_and_markdown_type():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(_fmt_params()), transport)
    assert _invoke(port, format="md").outcome == COMPLETED
    head, value = _parts(transport.calls[0])[FILE]
    assert 'filename="daily-newspaper-2026-10-11.md"' in head
    assert "Content-Type: text/markdown" in head
    assert value == EPUB


@pytest.mark.parametrize(
    ("fmt", "ctype"),
    [
        ("txt", "text/plain"),
        ("json", "application/json"),
        ("epub", "application/epub+zip"),
        ("pdf", "application/pdf"),
    ],
)
def test_each_known_format_maps_to_its_type(fmt, ctype):
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(_params(formats=[fmt])), transport)
    assert _invoke(port, format=fmt).outcome == COMPLETED
    head, _value = _parts(transport.calls[0])[FILE]
    assert f'filename="daily-newspaper-2026-10-11.{fmt}"' in head
    assert f"Content-Type: {ctype}" in head


@pytest.mark.parametrize("fmt", ["pdf", "MD", "", 3, "docx"])
def test_a_format_the_actor_does_not_list_is_refused_before_any_request(fmt):
    result = _refused(_store(_fmt_params()), format=fmt)
    assert result.error.startswith(INVALID_REQUEST)


def test_a_format_needs_the_actor_to_list_formats():
    result = _refused(format="md")
    assert result.error.startswith(INVALID_REQUEST)


def test_no_format_sends_epub_exactly_as_before():
    transport = FakeTransport(UPLOAD_OK, QUEUE_OK)
    port, _ = _port(_store(), transport)
    assert _invoke(port).outcome == COMPLETED
    head, _value = _parts(transport.calls[0])[FILE]
    assert 'filename="daily-newspaper-2026-10-11.epub"' in head
    assert "Content-Type: application/epub+zip" in head


def test_no_format_with_formats_that_exclude_epub_is_refused():
    result = _refused(_store(_params(formats=["md"])))
    assert result.error.startswith(INVALID_REQUEST)


@pytest.mark.parametrize("formats", [[], "md", ["MD"], ["docx"], [5]])
def test_bad_formats_on_the_actor_are_refused(formats):
    result = _refused(_store(_params(formats=formats)))
    assert result.error.startswith(INVALID_ACTOR)


def _replace_params(**over):
    return _params(**{"replace_path": REPLACE_PATH, "replace_method": "PUT", **over})


@pytest.mark.parametrize("method", ["PUT", "POST", "PATCH"])
def test_item_id_sends_the_bytes_to_the_replace_path_with_the_method(method):
    transport = FakeTransport(REPLACE_OK)
    port, _ = _port(_store(_replace_params(replace_method=method)), transport)
    result = _invoke(port, item_id=7)
    assert result.outcome == COMPLETED
    assert [(c.method, c.url) for c in transport.calls] == [(method, BASE + "/api/library/7/file")]
    assert _parts(transport.calls[0])[FILE][1] == EPUB
    assert transport.calls[0].headers["Authorization"] == "Bearer " + KEY_VALUE
    assert dict(result.output)["item_id"] == 7


def test_a_replace_does_not_queue():
    transport = FakeTransport(REPLACE_OK)
    port, _ = _port(_store(_replace_params()), transport)
    result = _invoke(port, item_id=7)
    assert len(transport.calls) == 1
    assert dict(result.output)["queue_id"] is None


def test_a_replace_needs_no_device():
    transport = FakeTransport(REPLACE_OK)
    port, _ = _port(_store(_replace_params(device_id=_DROP)), transport)
    assert _invoke(port, item_id=7).outcome == COMPLETED


def test_item_id_without_a_replace_path_is_invalid_actor():
    result = _refused(item_id=7)
    assert result.error.startswith(INVALID_ACTOR)


def test_item_id_without_a_replace_method_is_invalid_actor():
    result = _refused(_store(_params(replace_path=REPLACE_PATH)), item_id=7)
    assert result.error.startswith(INVALID_ACTOR)


@pytest.mark.parametrize("method", ["DELETE", "put", "GET", 5, ""])
def test_a_bad_replace_method_is_refused(method):
    result = _refused(_store(_replace_params(replace_method=method)), item_id=7)
    assert result.error.startswith(INVALID_ACTOR)


@pytest.mark.parametrize("item_id", [0, -1, "x", 1.5, True, [7]])
def test_a_bad_item_id_is_refused_before_any_request(item_id):
    result = _refused(_store(_replace_params()), item_id=item_id)
    assert result.error.startswith(INVALID_REQUEST)


@pytest.mark.parametrize(
    "path",
    [
        "/api/library",
        "/a/{item_id}/{item_id}",
        "/a/x{item_id}",
        "a/{item_id}",
        "/a/../{item_id}",
        "/a/{device_id}",
        3,
    ],
)
def test_a_bad_replace_path_is_refused(path):
    result = _refused(_store(_replace_params(replace_path=path)), item_id=7)
    assert result.error.startswith(INVALID_ACTOR)


def test_a_replace_reply_with_another_id_is_a_bad_response():
    transport = FakeTransport((200, {"item": {"id": 8}, "created": False}))
    port, _ = _port(_store(_replace_params()), transport)
    result = _invoke(port, item_id=7)
    assert result.outcome == FAILED
    assert result.error.startswith(BAD_RESPONSE)


def test_a_flat_replace_reply_with_another_id_is_a_bad_response():
    transport = FakeTransport((200, {"id": 8, "title": TITLE}))
    port, _ = _port(_store(_replace_params()), transport)
    result = _invoke(port, item_id=7)
    assert result.outcome == FAILED
    assert result.error.startswith(BAD_RESPONSE)


def test_a_replace_reply_wrapped_like_an_upload_is_accepted():
    transport = FakeTransport((200, {"item": {"id": 7}, "created": False}))
    port, _ = _port(_store(_replace_params()), transport)
    result = _invoke(port, item_id=7)
    assert result.outcome == COMPLETED
    assert result.output["item_id"] == 7


def test_a_replace_reply_naming_no_id_is_a_bad_response():
    transport = FakeTransport((200, {"title": TITLE}))
    port, _ = _port(_store(_replace_params()), transport)
    result = _invoke(port, item_id=7)
    assert result.outcome == FAILED
    assert result.error.startswith(BAD_RESPONSE)


def test_an_upload_reply_must_still_wrap_the_item():
    transport = FakeTransport((201, {"id": 7, "created": True}))
    port, _ = _port(_store(_params()), transport)
    result = _invoke(port)
    assert result.outcome == FAILED
    assert result.error.startswith(BAD_RESPONSE)


def test_a_failed_replace_names_the_status_and_code():
    transport = FakeTransport((404, {"code": "not_found"}))
    port, _ = _port(_store(_replace_params()), transport)
    result = _invoke(port, item_id=7)
    assert result.error == "upload_failed: http 404 not_found"
    assert result.retryable is False


def test_replace_and_format_combine():
    transport = FakeTransport(REPLACE_OK)
    port, _ = _port(_store(_replace_params(formats=["md"])), transport)
    assert _invoke(port, item_id=7, format="md").outcome == COMPLETED
    assert 'filename="daily-newspaper-2026-10-11.md"' in _parts(transport.calls[0])[FILE][0]


# ------------------------------------------------------------------ content_path (d7)


def _file_input(tmp_path, data=EPUB, name="a.pdf", **over):
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    (root / name).write_bytes(data)
    fields = {"content_base64": None, "content_path": str(root / name), "content_root": str(root)}
    fields.update(over)
    return {k: v for k, v in fields.items() if v is not None}, root


def _push_path(over_input, store=None):
    transport = FakeTransport((200, {"item": {"id": 7}, "created": True}), (200, {"id": 1}))
    port, _ = _port(store or _store(), transport)
    data = _input(**over_input)
    if over_input.get("content_base64", 0) is None:
        data.pop("content_base64")
    return port.invoke(data, "k1", DEADLINE, context=_ctx()), transport


def _path_refused(tmp_path, code, **over):
    fields, _ = _file_input(tmp_path)
    fields.update(over)
    fields = {k: v for k, v in fields.items() if v is not None}
    fields["content_base64"] = fields.get("content_base64")
    result, transport = _push_path({**fields, "content_base64": fields["content_base64"]})
    assert transport.calls == []
    assert result.outcome == FAILED
    assert result.error.startswith(code)
    assert result.retryable is False


def test_content_path_uploads_the_file_bytes(tmp_path):
    fields, _ = _file_input(tmp_path, data=b"%PDF-bytes")
    fields["content_base64"] = None
    result, transport = _push_path(fields)
    assert result.outcome == COMPLETED
    assert b"%PDF-bytes" in transport.calls[0].body


def test_content_path_outside_the_root_is_path_refused(tmp_path):
    other = tmp_path / "other.pdf"
    other.write_bytes(EPUB)
    _path_refused(tmp_path, "path_refused", content_path=str(other), content_base64=None)


def test_content_path_symlinked_file_is_path_refused(tmp_path):
    fields, root = _file_input(tmp_path)
    (root / "link.pdf").symlink_to(root / "a.pdf")
    _path_refused(tmp_path, "path_refused", content_path=str(root / "link.pdf"))


def test_content_path_symlinked_parent_is_path_refused(tmp_path):
    fields, root = _file_input(tmp_path)
    real = root / "real"
    real.mkdir()
    (real / "b.pdf").write_bytes(EPUB)
    (root / "dir").symlink_to(real)
    _path_refused(tmp_path, "path_refused", content_path=str(root / "dir" / "b.pdf"))


def test_content_path_missing_file_is_path_refused(tmp_path):
    fields, root = _file_input(tmp_path)
    _path_refused(tmp_path, "path_refused", content_path=str(root / "nope.pdf"))


def test_content_path_that_is_a_directory_is_path_refused(tmp_path):
    fields, root = _file_input(tmp_path)
    (root / "d").mkdir()
    _path_refused(tmp_path, "path_refused", content_path=str(root / "d"))


def test_content_path_needs_an_absolute_path_and_root(tmp_path):
    _path_refused(tmp_path, "path_refused", content_path="a.pdf")
    _path_refused(tmp_path, "path_refused", content_root="root")


def test_content_path_without_a_root_is_invalid(tmp_path):
    fields, _ = _file_input(tmp_path)
    fields.pop("content_root")
    fields["content_base64"] = None
    result, transport = _push_path(fields)
    assert transport.calls == []
    assert result.error.startswith(INVALID_REQUEST)


def test_both_sources_are_invalid(tmp_path):
    fields, _ = _file_input(tmp_path)
    fields["content_base64"] = _b64()
    result, transport = _push_path(fields)
    assert transport.calls == []
    assert result.error.startswith(INVALID_REQUEST)


def test_neither_source_is_invalid():
    result, transport = _push_path({"content_base64": None})
    assert transport.calls == []
    assert result.error.startswith(INVALID_REQUEST)


def test_content_root_alone_is_invalid(tmp_path):
    result, transport = _push_path({"content_base64": None, "content_root": str(tmp_path)})
    assert transport.calls == []
    assert result.error.startswith(INVALID_REQUEST)


def test_content_path_over_the_cap_is_too_large_like_base64(tmp_path, monkeypatch):
    monkeypatch.setattr(library_mod, "MAX_BYTES", 8)
    fields, _ = _file_input(tmp_path, data=b"123456789")
    fields["content_base64"] = None
    result, transport = _push_path(fields)
    assert transport.calls == []
    assert result.error.startswith(TOO_LARGE)


def test_content_path_with_replace_by_item_id(tmp_path):
    params = _params(replace_path="/api/library/{item_id}/file", replace_method="PUT")
    fields, _ = _file_input(tmp_path, data=b"%PDF-new")
    fields["content_base64"] = None
    fields["item_id"] = 5
    transport = FakeTransport((200, {"item": {"id": 5}}))
    port, _ = _port(_store(params), transport)
    data = _input(**fields)
    data.pop("content_base64")
    result = port.invoke(data, "k1", DEADLINE, context=_ctx())
    assert result.outcome == COMPLETED
    assert transport.calls[0].method == "PUT"
    assert transport.calls[0].url.endswith("/api/library/5/file")
    assert b"%PDF-new" in transport.calls[0].body
