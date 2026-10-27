"""http.call ``save_to``: the body is streamed to ``<root>/<sha256>.<ext>``, never returned."""

from __future__ import annotations

import hashlib
import os

import pytest

from culture_rules.engine.actorport import COMPLETED, FAILED
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.memory import MemoryStore

from .test_http_call import FakeResponse, _call, _invoke, _port

EPUB = b"PK\x03\x04 an epub body " * 50
EPUB_TYPE = {"Content-Type": "application/epub+zip"}


class ChunkedResponse(FakeResponse):
    """Hands the body out in the sizes asked for, as a real response does."""

    def __init__(self, status=200, body=b"", headers=None):
        super().__init__(status, body, headers)
        self._pos = 0

    def read(self, n=-1):
        end = len(self._body) if n < 0 else self._pos + n
        block = self._body[self._pos : end]
        self._pos += len(block)
        return block


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root(tmp_path, name="saved") -> str:
    root = os.path.join(os.path.realpath(tmp_path), name)
    os.mkdir(root)
    return root


def _store(save_roots=None, **policy):
    http_params = {"allow": ["api.example.com"], **policy}
    if save_roots is not None:
        http_params["save_roots"] = save_roots
    store = MemoryStore()
    store.put(
        ACTORS_COLLECTION,
        {
            "id": "svc",
            "name": "svc",
            "kind": "service",
            "enabled": True,
            "params": {"http": http_params},
        },
    )
    return store


def _save(root, body=EPUB, headers=None, status=200, save_roots=None, **policy):
    roots = [root] if save_roots is None else save_roots
    response = ChunkedResponse(status, body, headers or EPUB_TYPE)
    port, calls, lookups = _port(_store(roots, **policy), response=response)
    return port, calls, lookups


def _files(root) -> list[str]:
    return sorted(os.listdir(root))


# -- a 2xx body is saved -------------------------------------------------------------------


def test_a_2xx_body_is_written_to_root_sha256_ext(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root)
    res = _invoke(port, _call(save_to=root))
    assert res.outcome == COMPLETED
    with open(os.path.join(root, _sha(EPUB) + ".epub"), "rb") as fh:
        assert fh.read() == EPUB


def test_the_outputs_are_status_path_sha256_size_and_content_type(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root)
    res = _invoke(port, _call(save_to=root))
    assert res.output == {
        "status": 200,
        "path": os.path.join(root, _sha(EPUB) + ".epub"),
        "sha256": _sha(EPUB),
        "size": len(EPUB),
        "content_type": "application/epub+zip",
    }


def test_no_output_holds_the_body(tmp_path) -> None:
    root = _root(tmp_path)
    body = b"the secret body text"
    port, _, _ = _save(root, body=body, headers={"Content-Type": "text/plain"})
    res = _invoke(port, _call(save_to=root))
    assert "the secret body text" not in repr(res.output)


def test_only_the_saved_file_remains_in_the_root(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root)
    _invoke(port, _call(save_to=root))
    assert _files(root) == [_sha(EPUB) + ".epub"]


def test_save_to_may_name_the_root_with_a_trailing_slash(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root)
    res = _invoke(port, _call(save_to=root + "/"))
    assert res.outcome == COMPLETED


def test_the_same_bytes_again_reuse_the_file(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root)
    _invoke(port, _call(save_to=root), key="k1")
    port2, _, _ = _save(root)
    res = _invoke(port2, _call(save_to=root), key="k2")
    assert res.outcome == COMPLETED
    assert _files(root) == [_sha(EPUB) + ".epub"]


def test_a_root_among_several_save_roots_is_accepted(tmp_path) -> None:
    other = _root(tmp_path, "other")
    root = _root(tmp_path)
    port, _, _ = _save(root, save_roots=[other, root])
    res = _invoke(port, _call(save_to=root))
    assert res.output["path"] == os.path.join(root, _sha(EPUB) + ".epub")


# -- the extension comes from the Content-Type --------------------------------------------


@pytest.mark.parametrize(
    "content_type, ext",
    [
        ("application/epub+zip", "epub"),
        ("application/pdf", "pdf"),
        ("text/markdown", "md"),
        ("application/json", "json"),
        ("text/plain", "txt"),
        ("text/markdown; charset=utf-8", "md"),
        ("Application/PDF", "pdf"),
    ],
)
def test_the_extension_comes_from_the_content_type(tmp_path, content_type, ext) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, headers={"Content-Type": content_type})
    res = _invoke(port, _call(save_to=root))
    assert res.output["path"] == os.path.join(root, f"{_sha(EPUB)}.{ext}")


def test_content_type_output_drops_parameters(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, headers={"Content-Type": "text/plain; charset=utf-8"})
    res = _invoke(port, _call(save_to=root))
    assert res.output["content_type"] == "text/plain"


@pytest.mark.parametrize(
    "headers",
    [
        {"Content-Type": "text/html"},
        {"Content-Type": "application/octet-stream"},
        {"Content-Type": "image/png"},
        {"Content-Length": "5"},
    ],
)
def test_any_other_type_fails_unsupported_type(tmp_path, headers) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, headers=headers)
    res = _invoke(port, _call(save_to=root))
    assert res.outcome == FAILED
    assert res.error.startswith("unsupported_type")


def test_an_unsupported_type_writes_nothing(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, headers={"Content-Type": "text/html"})
    _invoke(port, _call(save_to=root))
    assert _files(root) == []


def test_an_unsupported_type_is_not_retryable(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, headers={"Content-Type": "text/html"})
    res = _invoke(port, _call(save_to=root))
    assert res.retryable is False


# -- refused before any request ------------------------------------------------------------


def test_save_to_outside_save_roots_fails_path_refused(tmp_path) -> None:
    root = _root(tmp_path)
    other = _root(tmp_path, "other")
    port, calls, _ = _save(root)
    res = _invoke(port, _call(save_to=other))
    assert res.error.startswith("path_refused")
    assert calls == []


def test_save_to_under_a_root_but_not_the_root_is_refused(tmp_path) -> None:
    root = _root(tmp_path)
    sub = os.path.join(root, "sub")
    os.mkdir(sub)
    port, calls, _ = _save(root)
    res = _invoke(port, _call(save_to=sub))
    assert res.error.startswith("path_refused")
    assert calls == []


def test_save_to_escaping_with_dotdot_is_refused(tmp_path) -> None:
    root = _root(tmp_path)
    _root(tmp_path, "other")
    port, calls, _ = _save(root)
    res = _invoke(port, _call(save_to=os.path.join(root, "..", "other")))
    assert res.error.startswith("path_refused")
    assert calls == []


def test_a_symlinked_root_is_refused(tmp_path) -> None:
    real = _root(tmp_path, "real")
    link = os.path.join(os.path.realpath(tmp_path), "link")
    os.symlink(real, link)
    port, calls, _ = _save(link)
    res = _invoke(port, _call(save_to=link))
    assert res.error.startswith("path_refused")
    assert calls == []


def test_a_root_under_a_symlinked_directory_is_refused(tmp_path) -> None:
    real = _root(tmp_path, "real")
    os.mkdir(os.path.join(real, "inner"))
    link = os.path.join(os.path.realpath(tmp_path), "link")
    os.symlink(real, link)
    root = os.path.join(link, "inner")
    port, calls, _ = _save(root)
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("path_refused")
    assert calls == []


def test_no_save_roots_fails_path_refused(tmp_path) -> None:
    root = _root(tmp_path)
    port, calls, _ = _port(_store(), response=ChunkedResponse(200, EPUB, EPUB_TYPE))
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("path_refused")
    assert calls == []


@pytest.mark.parametrize("save_roots", [[], "/tmp", ["relative/dir"], [7], {"a": "/tmp"}])
def test_save_roots_not_a_list_of_absolute_paths_fails_path_refused(tmp_path, save_roots):
    root = _root(tmp_path)
    port, calls, _ = _save(root, save_roots=save_roots)
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("path_refused")
    assert calls == []


def test_a_relative_save_to_fails_path_refused(tmp_path) -> None:
    root = _root(tmp_path)
    port, calls, _ = _save(root)
    res = _invoke(port, _call(save_to="saved"))
    assert res.error.startswith("path_refused")
    assert calls == []


def test_a_missing_root_fails_path_refused(tmp_path) -> None:
    root = os.path.join(os.path.realpath(tmp_path), "absent")
    port, calls, _ = _save(root)
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("path_refused")
    assert calls == []


@pytest.mark.parametrize("save_to", [7, "", ["/tmp"]])
def test_a_save_to_that_is_not_a_string_is_invalid(tmp_path, save_to) -> None:
    root = _root(tmp_path)
    port, calls, _ = _save(root)
    res = _invoke(port, _call(save_to=save_to))
    assert res.error.startswith("invalid_request")
    assert calls == []


def test_a_refused_save_to_is_not_resolved_either(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, lookups = _save(root)
    _invoke(port, _call(save_to="saved"))
    assert lookups == []


def test_a_refused_save_to_is_not_retryable(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root)
    res = _invoke(port, _call(save_to="saved"))
    assert res.retryable is False


def test_an_existing_symlink_at_the_target_name_is_refused(tmp_path) -> None:
    root = _root(tmp_path)
    elsewhere = os.path.join(os.path.realpath(tmp_path), "elsewhere")
    os.symlink(elsewhere, os.path.join(root, _sha(EPUB) + ".epub"))
    port, _, _ = _save(root)
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("path_refused")
    assert not os.path.lexists(elsewhere)


def test_a_symlink_at_the_target_name_leaves_no_temp_file(tmp_path) -> None:
    root = _root(tmp_path)
    os.symlink("/nonexistent", os.path.join(root, _sha(EPUB) + ".epub"))
    port, _, _ = _save(root)
    _invoke(port, _call(save_to=root))
    assert _files(root) == [_sha(EPUB) + ".epub"]


def test_a_directory_at_the_target_name_is_refused(tmp_path) -> None:
    root = _root(tmp_path)
    os.mkdir(os.path.join(root, _sha(EPUB) + ".epub"))
    port, _, _ = _save(root)
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("path_refused")


# -- the cap -------------------------------------------------------------------------------


def test_a_body_over_save_max_bytes_fails_too_large(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, save_max_bytes=len(EPUB) - 1)
    res = _invoke(port, _call(save_to=root))
    assert res.outcome == FAILED
    assert res.error.startswith("too_large")


def test_a_body_over_the_cap_leaves_no_file_or_temp_file(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, save_max_bytes=len(EPUB) - 1)
    _invoke(port, _call(save_to=root))
    assert _files(root) == []


def test_a_declared_length_over_the_cap_fails_too_large(tmp_path) -> None:
    root = _root(tmp_path)
    headers = {**EPUB_TYPE, "Content-Length": str(len(EPUB))}
    port, _, _ = _save(root, headers=headers, save_max_bytes=len(EPUB) - 1)
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("too_large")


def test_a_body_at_the_cap_is_saved(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, save_max_bytes=len(EPUB))
    res = _invoke(port, _call(save_to=root))
    assert res.output["size"] == len(EPUB)


def test_too_large_is_not_retryable(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, save_max_bytes=1)
    res = _invoke(port, _call(save_to=root))
    assert res.retryable is False


def test_the_default_cap_is_50_mib() -> None:
    from culture_rules.node.actions import http as http_mod

    assert http_mod.DEFAULT_SAVE_MAX_BYTES == 50 * 1024 * 1024


@pytest.mark.parametrize("cap", [0, -1, "5", True, 1.5])
def test_a_bad_save_max_bytes_fails_before_any_request(tmp_path, cap) -> None:
    root = _root(tmp_path)
    port, calls, _ = _save(root, save_max_bytes=cap)
    res = _invoke(port, _call(save_to=root))
    assert res.outcome == FAILED
    assert calls == []


def test_the_body_is_streamed_in_chunks(tmp_path) -> None:
    sizes: list[int] = []

    class Recording(ChunkedResponse):
        def read(self, n=-1):
            sizes.append(n)
            return super().read(n)

    root = _root(tmp_path)
    port, _, _ = _port(_store([root]), response=Recording(200, EPUB, EPUB_TYPE))
    _invoke(port, _call(save_to=root))
    assert -1 not in sizes


# -- 4xx/5xx are not saved -----------------------------------------------------------------


@pytest.mark.parametrize("status, retryable", [(404, False), (429, True), (500, True)])
def test_an_error_status_fails_as_http_call_does(tmp_path, status, retryable) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, status=status)
    res = _invoke(port, _call(save_to=root))
    assert res.error.startswith("http_status")
    assert res.retryable is retryable


@pytest.mark.parametrize("status", [404, 500])
def test_an_error_status_is_not_saved(tmp_path, status) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, status=status)
    _invoke(port, _call(save_to=root))
    assert _files(root) == []


# -- without save_to nothing changes ------------------------------------------------------


def test_without_save_to_the_body_is_returned_as_before(tmp_path) -> None:
    root = _root(tmp_path)
    port, _, _ = _save(root, body=b"hello", headers={"Content-Type": "text/plain"})
    res = _invoke(port, _call())
    assert res.output["body"] == "hello"
