"""Built-in attachment.fetch: pdf/epub attachments through http.call's vetted pinned opener."""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED, InvocationContext
from culture_rules.node.actions import http as http_mod
from culture_rules.node.actions.attachment_fetch import (
    ATTACHMENT_FETCH_BUILTIN,
    DEFAULT_ALLOWED_HOSTS,
    MAX_ATTACHMENT_BYTES,
    READ_CHUNK,
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
        self._pos = 0

    def read(self, n=-1):
        self.reads.append(n)
        end = len(self._body) if n < 0 else self._pos + n
        out, self._pos = self._body[self._pos : end], min(end, len(self._body))
        return out

    def close(self):
        pass


class FakeOpener:
    def __init__(self, calls, responder):
        self.calls = calls
        self.responder = responder

    def open(self, request, timeout=None):
        self.calls[-1]["url"] = request.full_url
        self.calls[-1]["timeout"] = timeout
        self.calls[-1]["user_agent"] = request.get_header("User-agent")
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

    kw.setdefault("base_root", ROOT)  # never the real ~/.local/share
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


ROOT: str = ""


@pytest.fixture(autouse=True)
def _root(tmp_path):
    global ROOT
    root = tmp_path / "attachments"
    root.mkdir()
    ROOT = str(root)
    return root


def _invoke(port, input, config=None, seconds=30):
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    return port.invoke(input, "k1", deadline, context=_ctx({"output_root": ROOT, **(config or {})}))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


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


def test_the_default_opener_names_culture_rules_as_its_user_agent() -> None:
    # the pinned opener's default, kept by a fetch request that sets none of its own
    assert http_mod.pinned_opener(PUBLIC).addheaders == [("User-agent", "culture-rules")]


def test_a_fetch_request_sets_no_user_agent_of_its_own() -> None:
    port, calls, _ = _port()
    _invoke(port, {"attachments": [_att()]})
    assert calls[0]["user_agent"] is None


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
    assert items[0] == {
        "filename": "paper.pdf",
        "skipped": False,
        "format": "pdf",
        "content_type": "application/pdf",
        "size": len(PDF),
        "sha256": _sha(PDF),
        "path": os.path.join(ROOT, _sha(PDF) + ".pdf"),
    }
    assert items[1]["path"] == os.path.join(ROOT, _sha(EPUB) + ".epub")
    assert items[1]["content_type"] == "application/epub+zip"
    with open(items[0]["path"], "rb") as f:
        assert f.read() == PDF
    with open(items[1]["path"], "rb") as f:
        assert f.read() == EPUB
    assert "content_base64" not in json.dumps(res.output)
    assert sorted(os.listdir(ROOT)) == sorted([_sha(PDF) + ".pdf", _sha(EPUB) + ".epub"])
    assert all(i["skipped"] is False for i in items)
    assert res.output["fetched"] == 2
    assert res.output["skipped"] == 0
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
    assert calls == []
    assert lookups == []


def test_default_hosts_refuse_any_other_host() -> None:
    port, calls, lookups = _port()
    res = _invoke(port, {"attachments": [_att(host="evil.example.com")]})
    assert res.outcome == COMPLETED
    item = res.output["items"][0]
    assert item["skipped"] is True
    assert item["reason"] == "host_refused"
    assert "path" not in item
    assert calls == []
    assert lookups == []


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
    assert item["skipped"] is True
    assert item["reason"] == "private_address"
    assert calls == []  # no connection made


def test_redirect_is_refused() -> None:
    port, calls, _ = _port(lambda url: http_mod.RedirectRefused(302))
    res = _invoke(port, {"attachments": [_att()]})
    item = res.output["items"][0]
    assert item["skipped"] is True
    assert item["reason"] == "redirect_refused"
    assert len(calls) == 1


def test_a_3xx_status_answer_is_refused_as_a_redirect() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=301, body=b""))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.output["items"][0]["reason"] == "redirect_refused"


def test_declared_size_over_50_mib_is_refused_without_a_request() -> None:
    port, calls, lookups = _port()
    res = _invoke(port, {"attachments": [_att(size=MAX_ATTACHMENT_BYTES + 1)]})
    item = res.output["items"][0]
    assert item["skipped"] is True
    assert item["reason"] == "too_large"
    assert calls == []
    assert lookups == []


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
    assert os.listdir(ROOT) == []  # the partial temp file is removed
    port, _, _ = _port(lambda url: FakeResponse(body=PDF[:8]), max_bytes=8)
    res = _invoke(port, {"attachments": [_att(size=None)]})
    assert res.output["items"][0]["skipped"] is False


def test_client_error_skips_the_item_with_http_status() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=404, body=b"gone"))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == COMPLETED
    assert res.output["items"][0]["reason"] == "http_status"


def test_a_client_error_skip_names_the_status() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=403, body=b"forbidden"))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.output["items"][0]["status"] == 403


def test_other_skips_name_no_status() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=301, body=b""))
    res = _invoke(port, {"attachments": [_att()]})
    assert "status" not in res.output["items"][0]


def test_server_and_network_errors_fail_the_step_retryable() -> None:
    port, _, _ = _port(lambda url: FakeResponse(status=503, body=b"down"))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == FAILED
    assert res.retryable is True
    port, _, _ = _port(lambda url: urllib.error.URLError("boom"))
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == FAILED
    assert res.retryable is True
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
    assert res.output["fetched"] == 1
    assert res.output["skipped"] == 2
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
        assert res.outcome == FAILED
        assert res.retryable is False
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


# --------------------------------------------------- d7: bytes on disk, not in run state


def test_download_is_streamed_in_chunks_and_capped_while_reading() -> None:
    body = b"%PDF" + b"x" * (3 * READ_CHUNK)
    response = FakeResponse(body=body)
    port, _, _ = _port(lambda url: response)
    res = _invoke(port, {"attachments": [_att(size=None)]})
    item = res.output["items"][0]
    assert item["size"] == len(body)
    assert item["sha256"] == _sha(body)
    assert all(0 < n <= READ_CHUNK for n in response.reads)
    assert len(response.reads) >= 3
    big = FakeResponse(body=b"x" * (READ_CHUNK * 4))
    port, _, _ = _port(lambda url: big, max_bytes=READ_CHUNK + 10)
    res = _invoke(port, {"attachments": [_att(size=None)]})
    assert res.output["items"][0]["reason"] == "too_large"
    assert sum(big.reads) <= 2 * READ_CHUNK  # stopped once over the cap, not read to the end
    assert os.listdir(ROOT) == [_sha(body) + ".pdf"]  # only the first fetch; no temp file left


def test_same_bytes_again_reuse_the_file_and_refresh_its_age() -> None:
    port, _, _ = _port()
    first = _invoke(port, {"attachments": [_att()]}).output["items"][0]
    os.utime(first["path"], (1_000_000, 1_000_000))
    again = _invoke(port, {"attachments": [_att("copy.pdf")]}).output["items"][0]
    assert again["path"] == first["path"]
    assert again["sha256"] == first["sha256"]
    assert again["filename"] == "copy.pdf"
    assert os.listdir(ROOT) == [_sha(PDF) + ".pdf"]
    assert os.stat(first["path"]).st_mtime > 1_000_000


@pytest.mark.parametrize("value", ["", "relative/dir", 7])
def test_output_root_must_be_an_absolute_path(value) -> None:
    port, calls, _ = _port()
    res = _invoke(port, {"attachments": [_att()]}, config={"output_root": value})
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("path_refused")
    assert calls == []


def test_output_root_must_exist_as_a_directory(tmp_path) -> None:
    port, _, _ = _port()
    res = _invoke(port, {"attachments": [_att()]}, config={"output_root": str(tmp_path / "no")})
    assert res.error.startswith("path_refused")
    afile = tmp_path / "file"
    afile.write_text("x")
    res = _invoke(port, {"attachments": [_att()]}, config={"output_root": str(afile)})
    assert res.error.startswith("path_refused")


def test_a_symlink_at_the_target_name_is_refused(tmp_path) -> None:
    elsewhere = tmp_path / "elsewhere.pdf"
    elsewhere.write_bytes(b"other")
    os.symlink(elsewhere, os.path.join(ROOT, _sha(PDF) + ".pdf"))
    port, _, _ = _port()
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("path_refused")
    assert elsewhere.read_bytes() == b"other"
    assert [n for n in os.listdir(ROOT) if n.startswith(".")] == []


def _age(path, days):
    when = datetime.now(UTC).timestamp() - days * 86400
    os.utime(path, (when, when), follow_symlinks=False)


def test_retention_deletes_only_old_fetched_files(tmp_path) -> None:
    root = ROOT
    old_pdf = os.path.join(root, "a" * 64 + ".pdf")
    old_epub = os.path.join(root, "b" * 64 + ".epub")
    young = os.path.join(root, "c" * 64 + ".pdf")
    other = os.path.join(root, "notes.pdf")
    upper = os.path.join(root, "D" * 64 + ".pdf")  # not the lowercase hex pattern
    txt = os.path.join(root, "e" * 64 + ".txt")
    for p in (old_pdf, old_epub, young, other, upper, txt):
        with open(p, "wb") as f:
            f.write(b"x")
    target = tmp_path / "target.pdf"
    target.write_bytes(b"keep")
    link = os.path.join(root, "f" * 64 + ".pdf")
    os.symlink(target, link)
    adir = os.path.join(root, "9" * 64 + ".epub")
    os.mkdir(adir)
    for p in (old_pdf, old_epub, other, upper, txt, link, adir):
        _age(p, 10)
    _age(young, 3)
    port, _, _ = _port()
    res = _invoke(port, {"attachments": []})
    assert res.outcome == COMPLETED
    left = set(os.listdir(root))
    assert os.path.basename(old_pdf) not in left
    assert os.path.basename(old_epub) not in left
    for p in (young, other, upper, txt, link, adir):
        assert os.path.basename(p) in left
    assert target.read_bytes() == b"keep"


def test_retention_days_is_configurable_and_bounded() -> None:
    old = os.path.join(ROOT, "a" * 64 + ".pdf")
    with open(old, "wb") as f:
        f.write(b"x")
    _age(old, 3)
    port, _, _ = _port()
    _invoke(port, {"attachments": [], "retention_days": 5})
    assert os.path.exists(old)
    _invoke(port, {"attachments": [], "retention_days": 2})
    assert not os.path.exists(old)
    for bad in (0, 366, "7", True, 1.5):
        res = _invoke(port, {"attachments": [], "retention_days": bad})
        assert res.outcome == FAILED
        assert res.error.startswith("bad_params")


def test_the_file_just_fetched_survives_its_own_retention_pass() -> None:
    port, _, _ = _port()
    res = _invoke(port, {"attachments": [_att()], "retention_days": 1})
    assert os.path.exists(res.output["items"][0]["path"])


def test_a_file_removed_between_the_check_and_the_touch_is_placed_again(monkeypatch) -> None:
    # review of the R2 diff: a lost race must not escape as an unclassified OSError
    port, _, _ = _port()
    first = _invoke(port, {"attachments": [_att()]}).output["items"][0]
    real = os.utime

    def gone(path, *a, **kw):
        if str(path) == first["path"]:
            os.unlink(path)
            raise FileNotFoundError(path)
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "utime", gone)
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == COMPLETED, res.error
    assert os.path.isfile(first["path"])
    assert [n for n in os.listdir(ROOT) if n.endswith(".part")] == []


def test_a_write_error_fails_the_step_retryable_and_leaves_no_temp_file(monkeypatch) -> None:
    port, _, _ = _port()

    def full(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", full)
    res = _invoke(port, {"attachments": [_att()]})
    assert res.outcome == FAILED
    assert res.retryable is True
    assert res.error.startswith("write_failed")
    assert os.listdir(ROOT) == []


# ------------------------------------------------------------------ confinement (review of #102)


@pytest.mark.parametrize("literal", ["127.0.0.1", "10.0.0.5", "::1", "[::1]", "162.159.130.233"])
def test_an_ip_literal_in_allowed_hosts_is_bad_params(literal) -> None:
    port, calls, lookups = _port()
    url = f"https://{literal}/a/paper.pdf"
    res = _invoke(port, {"attachments": [{**_att(), "url": url}]}, {"allowed_hosts": [literal]})
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("bad_params")
    assert calls == []
    assert lookups == []


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "::1"])
def test_a_listed_hostname_resolving_to_a_private_address_is_refused(address) -> None:
    port, calls, _ = _port(resolved=[address])
    res = _invoke(
        port,
        {"attachments": [_att(host="files.example.org")]},
        {"allowed_hosts": ["files.example.org"]},
    )
    assert res.outcome == COMPLETED
    assert res.output["items"][0]["reason"] == "private_address"
    assert calls == []


def test_output_root_outside_the_base_is_path_refused(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    port, calls, _ = _port()
    res = _invoke(port, {"attachments": [_att()]}, {"output_root": str(outside)})
    assert res.outcome == FAILED
    assert res.retryable is False
    assert res.error.startswith("path_refused")
    assert calls == []
    assert os.listdir(outside) == []


def test_output_root_of_slash_is_path_refused() -> None:
    port, calls, _ = _port()
    res = _invoke(port, {"attachments": [], "retention_days": 1}, {"output_root": "/"})
    assert res.error.startswith("path_refused")
    assert calls == []


def test_retention_never_runs_outside_the_base(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / ("a" * 64 + ".pdf")
    victim.write_bytes(b"x")
    _age(victim, 30)
    port, _, _ = _port()
    _invoke(port, {"attachments": [], "retention_days": 1}, {"output_root": str(outside)})
    assert victim.exists()


def test_a_symlink_in_the_base_pointing_outside_is_path_refused(tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = os.path.join(ROOT, "escape")
    os.symlink(outside, link)
    port, calls, _ = _port()
    res = _invoke(port, {"attachments": [_att()]}, {"output_root": link})
    assert res.error.startswith("path_refused")
    assert calls == []


def test_absent_output_root_writes_to_the_base() -> None:
    port, _, _ = _port()
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    res = port.invoke({"attachments": [_att()]}, "k1", deadline, context=_ctx())
    assert res.outcome == COMPLETED, res.error
    path = res.output["items"][0]["path"]
    assert os.path.dirname(path) == os.path.realpath(ROOT)


def test_an_output_root_under_the_base_is_accepted() -> None:
    sub = os.path.join(ROOT, "discord")
    os.mkdir(sub)
    port, _, _ = _port()
    res = _invoke(port, {"attachments": [_att()]}, {"output_root": sub})
    assert res.outcome == COMPLETED, res.error
    assert os.path.dirname(res.output["items"][0]["path"]) == os.path.realpath(sub)


def test_a_missing_base_is_created_private(tmp_path) -> None:
    base = tmp_path / "fresh" / "attachments"
    port, _, _ = _port(base_root=str(base))
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    res = port.invoke({"attachments": [_att()]}, "k1", deadline, context=_ctx())
    assert res.outcome == COMPLETED, res.error
    assert base.is_dir()
    assert base.stat().st_mode & 0o777 == 0o700


def test_the_base_comes_from_the_environment(tmp_path, monkeypatch) -> None:
    base = tmp_path / "from-env"
    monkeypatch.setenv("CULTURE_RULES_ATTACHMENTS_ROOT", str(base))
    port, _, _ = _port(base_root=None)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    res = port.invoke({"attachments": [_att()]}, "k1", deadline, context=_ctx())
    assert res.outcome == COMPLETED, res.error
    assert os.path.dirname(res.output["items"][0]["path"]) == os.path.realpath(base)


def test_the_default_base_is_under_the_home_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("CULTURE_RULES_ATTACHMENTS_ROOT", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    port, _, _ = _port(base_root=None)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    res = port.invoke({"attachments": []}, "k1", deadline, context=_ctx())
    assert res.outcome == COMPLETED, res.error
    assert (tmp_path / "home" / ".local/share/culture-rules/attachments").is_dir()


def test_a_relative_base_from_the_environment_is_path_refused(monkeypatch) -> None:
    monkeypatch.setenv("CULTURE_RULES_ATTACHMENTS_ROOT", "relative/attachments")
    port, calls, _ = _port(base_root=None)
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    res = port.invoke({"attachments": [_att()]}, "k1", deadline, context=_ctx())
    assert res.error.startswith("path_refused")
    assert calls == []


MD = b"# Runbook\n\nSteps.\n"


def _md_port():
    return _port(responder=lambda url: FakeResponse(body=MD))


@pytest.mark.parametrize(
    "name, ctype",
    [
        ("runbook.md", "text/markdown; charset=utf-8"),  # what Discord sent live (d10)
        ("runbook.md", "text/markdown"),
        ("runbook.md", None),  # no type: the .md name decides
        ("runbook.md", "application/octet-stream"),
    ],
)
def test_a_markdown_attachment_is_fetched_as_md(name, ctype) -> None:
    port, _, _ = _md_port()
    item = _invoke(port, {"attachments": [_att(name, ctype)]}).output["items"][0]
    assert item["skipped"] is False
    assert item["format"] == "md"
    assert item["content_type"] == "text/markdown"
    assert item["path"].endswith(".md")
    assert open(item["path"], "rb").read() == MD


def test_a_markdown_name_with_an_image_type_is_still_skipped() -> None:
    port, calls, _ = _md_port()
    item = _invoke(port, {"attachments": [_att("runbook.md", "image/png")]}).output["items"][0]
    assert item["reason"] == "unsupported_format"
    assert calls == []


def test_retention_also_clears_old_markdown_files() -> None:
    port, _, _ = _md_port()
    old = os.path.join(ROOT, "a" * 64 + ".md")
    open(old, "wb").write(MD)
    os.utime(old, (1_000_000, 1_000_000))
    _invoke(port, {"attachments": []})
    assert not os.path.exists(old)
