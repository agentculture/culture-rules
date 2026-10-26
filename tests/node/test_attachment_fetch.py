"""Built-in attachment.fetch: pdf/epub attachments through http.call's vetted pinned opener."""

from __future__ import annotations

import base64
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import http as http_mod
from culture_rules.node.actions.attachment_fetch import (
    ATTACHMENT_FETCH_BUILTIN,
    DEFAULT_ALLOWED_HOSTS,
    MAX_ATTACHMENT_BYTES,
    AttachmentFetchPort,
)

PUBLIC = "162.159.130.233"
PDF = b"%PDF-1.7\n...pdf bytes...\n%%EOF\n"
EPUB = b"PK\x03\x04mimetypeapplication/epub+zip..."


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status = status
        self._body = body
        self.headers = headers if headers is not None else {}
        self.reads: list[int] = []

    def read(self, n=-1):
        self.reads.append(n)
        return self._body if n < 0 else self._body[:n]

    def close(self):
        pass


class FakeOpener:
    def __init__(self, calls, responder):
        self.calls = calls
        self.responder = responder

    def open(self, request, timeout=None):
        self.calls[-1]["url"] = request.full_url
        self.calls[-1]["timeout"] = timeout
        out = self.responder(request.full_url)
        if isinstance(out, BaseException):
            raise out
        return out


def _port(responder=None, resolved=(PUBLIC,), **kw):
    calls: list[dict] = []
    lookups: list[tuple[str, int]] = []
    bodies = {"pdf": PDF, "epub": EPUB}

    def default(url):
        return FakeResponse(body=bodies["epub" if ".epub?" in url else "pdf"])

    def resolver(host, port):
        lookups.append((host, port))
        return list(resolved(host) if callable(resolved) else resolved)

    def opener_factory(pinned):
        calls.append({"pinned": pinned})
        return FakeOpener(calls, responder or default)

    port = AttachmentFetchPort(resolver=resolver, opener_factory=opener_factory, **kw)
    return port, calls, lookups


def _ctx(config=None):
    return InvocationContext(
        run_id="run1",
        step_id="fetch",
        kind="code",
        host="h1",
        config={"builtin": ATTACHMENT_FETCH_BUILTIN, **(config or {})},
    )


def _invoke(port, input, config=None, seconds=30):
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    return port.invoke(input, "k1", deadline, context=_ctx(config))


def _att(name="paper.pdf", ctype="application/pdf", host="cdn.discordapp.com", **extra):
    # the shape discord.read outputs per attachment
    return {
        "id": "111",
        "filename": name,
        "content_type": ctype,
        "size": 1234,
        "url": f"https://{host}/attachments/2/111/{name}?ex=abc&is=def&hm=0123",
        **extra,
    }


def test_builtin_name_and_defaults() -> None:
    assert ATTACHMENT_FETCH_BUILTIN == "attachment.fetch"
    assert DEFAULT_ALLOWED_HOSTS == ("cdn.discordapp.com", "media.discordapp.net")
    assert MAX_ATTACHMENT_BYTES == 50 * 1024 * 1024


def test_pdf_and_epub_are_fetched_as_base64_with_their_format() -> None:
    port, calls, lookups = _port()
    res = _invoke(
        port,
        {
            "attachments": [
                _att(),
                _att("book.epub", "application/epub+zip", host="media.discordapp.net"),
            ]
        },
    )
    assert res.outcome == COMPLETED
    items = res.output["items"]
    assert [i["format"] for i in items] == ["pdf", "epub"]
    assert base64.b64decode(items[0]["content_base64"]) == PDF
    assert base64.b64decode(items[1]["content_base64"]) == EPUB
    assert items[0]["filename"] == "paper.pdf"
    assert items[0]["size"] == len(PDF)
    assert all(i["skipped"] is False for i in items)
    assert res.output["fetched"] == 2 and res.output["skipped"] == 0
    # dialled the vetted address, not the hostname
    assert [c["pinned"] for c in calls] == [PUBLIC, PUBLIC]
    assert lookups == [("cdn.discordapp.com", 443), ("media.discordapp.net", 443)]


@pytest.mark.parametrize(
    "name,ctype,fmt",
    [
        ("x.bin", "application/pdf; charset=binary", "pdf"),
        ("Paper.PDF", None, "pdf"),
        ("paper.pdf", "application/octet-stream", "pdf"),
        ("book.epub", "", "epub"),
        ("noext", "application/epub+zip", "epub"),
    ],
)
def test_format_from_content_type_or_extension(name, ctype, fmt) -> None:
    port, _, _ = _port(lambda url: FakeResponse(body=PDF))
    res = _invoke(port, {"attachments": [_att(name, ctype)]})
    assert res.output["items"][0]["format"] == fmt


@pytest.mark.parametrize(
    "name,ctype",
    [
        ("cat.png", "image/png"),
        ("photo.jpg", "image/jpeg"),
        ("clip.mp4", "video/mp4"),
        ("notes.txt", "text/plain; charset=utf-8"),
        ("archive.zip", "application/zip"),
        ("noext", None),
        ("fake.pdf", "image/png"),  # a declared image is an image, whatever the name
    ],
)
def test_images_and_other_types_are_skipped_unsupported_without_a_request(name, ctype) -> None:
    port, calls, lookups = _port()
    res = _invoke(port, {"attachments": [_att(name, ctype)]})
    assert res.outcome == COMPLETED
    item = res.output["items"][0]
    assert item == {"filename": name, "skipped": True, "reason": "unsupported_format"}
    assert calls == [] and lookups == []


def test_default_hosts_refuse_any_other_host() -> None:
    port, calls, lookups = _port()
    res = _invoke(port, {"attachments": [_att(host="evil.example.com")]})
    assert res.outcome == COMPLETED
    item = res.output["items"][0]
    assert item["skipped"] is True and item["reason"] == "host_refused"
    assert "content_base64" not in item
    assert calls == [] and lookups == []


def test_allowed_hosts_input_replaces_the_default() -> None:
    port, calls, _ = _port()
    res = _invoke(
        port,
        {"attachments": [_att(host="files.example.org"), _att()]},
        config={"allowed_hosts": ["files.example.org"]},
    )
    items = res.output["items"]
    assert items[0]["skipped"] is False
    assert items[1]["reason"] == "host_refused"  # the default is replaced, not extended
    assert len(calls) == 1


def test_non_https_and_credentials_are_refused() -> None:
    port, calls, _ = _port()
    res = _invoke(
        port,
        {
            "attachments": [
                {**_att(), "url": "http://cdn.discordapp.com/a/paper.pdf"},
                {**_att(), "url": "https://u:p@cdn.discordapp.com/a/paper.pdf"},
                {**_att(), "url": "file:///etc/passwd"},
            ]
        },
    )
    assert [i["reason"] for i in res.output["items"]] == ["host_refused"] * 3
    assert calls == []


@pytest.mark.parametrize("address", ["10.0.0.5", "127.0.0.1", "169.254.169.254", "fd00::1"])
def test_host_resolving_to_a_private_address_is_refused(address) -> None:
    port, calls, _ = _port(resolved=[PUBLIC, address])
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == COMPLETED
    item = res.output["items"][0]
    assert item["skipped"] is True and item["reason"] == "private_address"
    assert calls == []  # no connection made


def test_redirect_is_refused() -> None:
    port, calls, _ = _port(lambda url: http_mod.RedirectRefused(302))
    res = _invoke(port, {"attachments": [_att()]})
    item = res.output["items"][0]
    assert item["skipped"] is True and item["reason"] == "redirect_refused"
    assert len(calls) == 1


def test_a_3xx_status_answer_is_refused_as_a_redirect() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=301, body=b""))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.output["items"][0]["reason"] == "redirect_refused"


def test_declared_size_over_50_mib_is_refused_without_a_request() -> None:
    port, calls, lookups = _port()
    res = _invoke(port, {"attachments": [_att(size=MAX_ATTACHMENT_BYTES + 1)]})
    item = res.output["items"][0]
    assert item["skipped"] is True and item["reason"] == "too_large"
    assert calls == [] and lookups == []


def test_content_length_over_50_mib_is_refused_before_reading() -> None:
    response = FakeResponse(body=PDF, headers={"Content-Length": str(MAX_ATTACHMENT_BYTES + 1)})
    port, _, _ = _port(lambda url: response)
    res = _invoke(port, {"attachments": [_att()]})
    assert res.output["items"][0]["reason"] == "too_large"
    assert response.reads == []


def test_body_over_the_limit_is_refused_even_when_undeclared() -> None:
    port, _, _ = _port(lambda url: FakeResponse(body=PDF), max_bytes=8)
    res = _invoke(port, {"attachments": [_att(size=None)]})
    assert res.output["items"][0]["reason"] == "too_large"
    port, _, _ = _port(lambda url: FakeResponse(body=PDF[:8]), max_bytes=8)
    res = _invoke(port, {"attachments": [_att(size=None)]})
    assert res.output["items"][0]["skipped"] is False


def test_client_error_skips_the_item_with_http_status() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=404, body=b"gone"))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == COMPLETED
    assert res.output["items"][0]["reason"] == "http_status"


def test_server_and_network_errors_fail_the_step_retryable() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=503, body=b"down"))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == FAILED and res.retryable is True
    port, _, _ = _port(lambda url: urllib.error.URLError("boom"))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == FAILED and res.retryable is True
    assert res.error.startswith("network_error")


def test_mixed_list_keeps_order_and_counts() -> None:
    port, calls, _ = _port()
    res = _invoke(
        port,
        {
            "attachments": [
                _att("cat.png", "image/png"),
                _att(),
                _att(host="evil.example.com"),
            ]
        },
    )
    items = res.output["items"]
    assert [i["filename"] for i in items] == ["cat.png", "paper.pdf", "paper.pdf"]
    assert [i["skipped"] for i in items] == [True, False, True]
    assert res.output["fetched"] == 1 and res.output["skipped"] == 2
    assert len(calls) == 1


def test_empty_list_is_an_empty_result() -> None:
    port, _, _ = _port()
    res = _invoke(port, {"attachments": []})
    assert res.outcome == COMPLETED
    assert res.output == {"items": [], "fetched": 0, "skipped": 0}


def test_bad_params_fail_non_retryable() -> None:
    port, _, _ = _port()
    for bad in ({}, {"attachments": "x"}, {"attachments": [], "allowed_hosts": "cdn"}):
        res = _invoke(port, bad)
        assert res.outcome == FAILED and res.retryable is False
        assert res.error.startswith("bad_params")


def test_an_item_without_a_url_is_skipped() -> None:
    port, calls, _ = _port()
    res = _invoke(port, {"attachments": [{"filename": "paper.pdf"}, "junk"]})
    assert [i["reason"] for i in res.output["items"]] == ["invalid_item", "invalid_item"]
    assert calls == []


def test_attachments_may_sit_in_config() -> None:
    port, _, _ = _port()
    res = _invoke(port, {}, config={"attachments": [_att()]})
    assert res.output["fetched"] == 1
