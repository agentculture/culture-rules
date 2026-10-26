"""Built-in ``attachment.fetch``: download pdf and epub attachments, vetted (discord-surfaces R2).

A code-step built-in (``config.builtin: attachment.fetch``), like ``markdown.render``: it
fetches public CDN URLs and needs no secret, so no actor is involved. Because anyone who can
edit a workflow writes its steps, nothing a step supplies widens what the node touches. The
connection policy reuses ``http.call``'s pieces from :mod:`culture_rules.node.actions.http`
(not copied): the URL checks, DNS resolution with every address vetted against the refused
(private, loopback, link-local, CGNAT, ...) ranges - with no allow-list exception, unlike
``http.call`` - a connection pinned to the vetted address, no proxies and no redirects.

The bytes never enter run state (deviation d7: a run document is capped at 16 MiB). Each
fetched file is streamed in :data:`READ_CHUNK` pieces to a temporary file in the output root
(the cap enforced while reading), hashed on the way, then renamed to
``<output root>/<sha256>.<pdf|epub>`` - an atomic write. The same bytes again find that file
and reuse it (its age is refreshed); a symlink or non-file at that name is ``path_refused``.
A later step (``library.push`` with ``content_path`` + ``content_root``) reads the file; its
library actor must list the attachments root in its ``content_roots``.

The node's attachments root (the base) is the environment variable
:data:`ATTACHMENTS_ROOT_ENV` (``CULTURE_RULES_ATTACHMENTS_ROOT``), else
:data:`DEFAULT_ATTACHMENTS_ROOT` (``~/.local/share/culture-rules/attachments``); ``~`` is
expanded, the path must be absolute, and a missing base is created with mode ``0o700``.
The port's ``base_root=`` argument replaces both (tests pass a temporary directory).

Inputs (each may instead sit in ``config``; the input wins):

* ``output_root`` (optional; absent means the base itself): an absolute path to an existing
  directory that resolves (symlinks followed, ``os.path.realpath``) to the base or a
  directory under it, else the step fails ``path_refused`` before anything is fetched or
  deleted; every file is written directly inside the resolved directory;
* ``retention_days`` (optional, an integer 1-365, default 7): on each call, before fetching,
  files in the output root named ``<64 lowercase hex>.pdf`` or ``.epub`` that are regular
  files (never symlinks) and older than this are deleted; nothing else there is touched;
* ``attachments``: a list of ``{url, filename, content_type}`` items - the shape
  ``discord.read`` outputs per attachment (its ``id`` and ``size`` are read when present);
* ``allowed_hosts`` (optional): exact hostnames to fetch from (an IP literal is refused
  ``bad_params``; every address a host resolves to is still vetted); it replaces the default
  :data:`DEFAULT_ALLOWED_HOSTS` (Discord's CDN hosts).

The format comes from ``content_type`` (``application/pdf``, ``application/epub+zip``) or,
when the type is absent or ``application/octet-stream``, from the filename's extension
(``.pdf``, ``.epub``). Anything else - images included - is not fetched.

Output: ``items`` (one per attachment, in order), ``fetched`` and ``skipped`` (counts). A
fetched item is ``{filename, skipped: false, format, content_type, size, sha256, path}``
(``path`` is the absolute path of the stored file, ``sha256`` its hex digest, ``size`` its
bytes); there is no ``content_base64``;
a skipped one is ``{filename, skipped: true, reason}``, the reason one of:

* ``unsupported_format`` - not a pdf or an epub (no request made);
* ``invalid_item`` - not a mapping, or no url;
* ``host_refused`` - not ``https``, credentials in the url, or a host not allowed;
* ``private_address`` - the host resolves to a refused address (no connection made);
* ``redirect_refused`` - the server answered a redirect (never followed);
* ``too_large`` - over :data:`MAX_ATTACHMENT_BYTES` by the declared ``size``, the
  ``Content-Length`` or the bytes read (never more than the limit plus one is read);
* ``http_status`` - a 4xx answer.

A 5xx or 429 answer, a network error and a resolution failure fail the whole step,
retryable, so a retry fetches again (files already stored are reused). Non-list
``attachments`` or ``allowed_hosts``, an IP literal in ``allowed_hosts``, or a
``retention_days`` out of range, fail ``bad_params``; a bad base, an ``output_root`` outside
the base, or a symlink at a target name fails ``path_refused`` (neither retryable). Nothing
is logged. Standard library only.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.node.actions.http import (
    MAX_TIMEOUT,
    OpenerFactory,
    RedirectRefused,
    Resolver,
    _checked_url,
    _is_ip,
    _norm_host,
    _Refused,
    _vet_address,
    pinned_opener,
    resolve_host,
)

__all__ = [
    "ATTACHMENTS_ROOT_ENV",
    "ATTACHMENT_FETCH_BUILTIN",
    "DEFAULT_ATTACHMENTS_ROOT",
    "DEFAULT_ALLOWED_HOSTS",
    "MAX_ATTACHMENT_BYTES",
    "READ_CHUNK",
    "AttachmentFetchPort",
]

ATTACHMENT_FETCH_BUILTIN = "attachment.fetch"
ATTACHMENTS_ROOT_ENV = "CULTURE_RULES_ATTACHMENTS_ROOT"
DEFAULT_ATTACHMENTS_ROOT = "~/.local/share/culture-rules/attachments"
DEFAULT_ALLOWED_HOSTS = ("cdn.discordapp.com", "media.discordapp.net")
MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024
READ_CHUNK = 1024 * 1024
DEFAULT_RETENTION_DAYS = 7
_RETENTION_BOUNDS = (1, 365)
_STORED_NAME = re.compile(r"[0-9a-f]{64}\.(?:pdf|epub)")
_PATH_REFUSED = "path_refused"
_HTTPS_PORT = 443
_BAD_PARAMS = "bad_params"
_TYPES = {"application/pdf": "pdf", "application/epub+zip": "epub"}
_EXTENSIONS = {".pdf": "pdf", ".epub": "epub"}
_GENERIC_TYPES = frozenset({"", "application/octet-stream"})
_RETRYABLE_STATUS = frozenset({408, 425, 429})


class _BadParams(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"{_BAD_PARAMS}: {message}")


class _Skip(Exception):
    """One attachment is not fetched; ``reason`` is the item's reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _StepFailed(Exception):
    """A transient failure: the whole step fails retryable."""


class _PathRefused(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(f"{_PATH_REFUSED}: {message}")


def _base_root(value: str) -> Path:
    """The resolved base (the node's attachments root), created ``0o700`` when missing."""
    expanded = os.path.expanduser(value)
    if not os.path.isabs(expanded):
        raise _PathRefused("the attachments root must be an absolute path")
    try:
        os.makedirs(expanded, mode=0o700, exist_ok=True)
        base = Path(expanded).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _PathRefused("the attachments root cannot be created") from exc
    if not base.is_dir():
        raise _PathRefused("the attachments root is not a directory")
    return base


def _output_root(value: Any, base: Path) -> Path:
    """The resolved ``output_root`` (the base when absent): an existing directory at or under
    the base, else :class:`_PathRefused`."""
    if value is None:
        return base
    if not isinstance(value, str) or not os.path.isabs(value):
        raise _PathRefused("output_root must be an absolute path")
    try:
        root = Path(value).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _PathRefused("output_root does not exist") from exc
    if not root.is_relative_to(base):
        raise _PathRefused("output_root must be the attachments root or under it")
    if not root.is_dir():
        raise _PathRefused("output_root is not a directory")
    return root


def _retention_days(value: Any) -> int:
    if value is None:
        return DEFAULT_RETENTION_DAYS
    low, high = _RETENTION_BOUNDS
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise _BadParams(f"retention_days must be an integer from {low} to {high}")
    return value


def _param(input: Mapping[str, Any], config: Mapping[str, Any], key: str) -> Any:
    value = input.get(key)
    return config.get(key) if value is None else value


def _format(filename: Any, content_type: Any) -> str | None:
    """``pdf``, ``epub`` or None (unsupported), from the content type, else the extension."""
    ctype = content_type.split(";", 1)[0].strip().lower() if isinstance(content_type, str) else ""
    if ctype in _TYPES:
        return _TYPES[ctype]
    if ctype not in _GENERIC_TYPES or not isinstance(filename, str):
        return None
    dot = filename.rfind(".")
    return _EXTENSIONS.get(filename[dot:].lower()) if dot >= 0 else None


def _header(headers: Any, name: str) -> str | None:
    for key, value in getattr(headers, "items", lambda: [])():
        if str(key).lower() == name:
            return str(value)
    return None


def _over(value: Any, limit: int) -> bool:
    """Whether a declared size (int or numeric string) exceeds ``limit``."""
    if isinstance(value, bool):
        return False
    try:
        return int(value) > limit
    except (TypeError, ValueError):
        return False


class AttachmentFetchPort:
    """Built-in ``attachment.fetch`` (see the module docstring)."""

    supports_idempotency_key = True  # a GET of the same URLs is safe to repeat

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        opener_factory: OpenerFactory | None = None,
        max_bytes: int = MAX_ATTACHMENT_BYTES,
        clock: Callable[[], datetime] | None = None,
        base_root: str | os.PathLike[str] | None = None,
    ) -> None:
        if base_root is None:
            base_root = os.environ.get(ATTACHMENTS_ROOT_ENV) or DEFAULT_ATTACHMENTS_ROOT
        self._base_root = os.fspath(base_root)
        self._resolver = resolver or resolve_host
        self._opener_factory = opener_factory or pinned_opener
        self._max_bytes = max_bytes
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],  # noqa: A002 - the ActorPort signature
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            attachments, allow = self._params(input, config)
            days = _retention_days(_param(input, config, "retention_days"))
            base = _base_root(self._base_root)
            root = _output_root(_param(input, config, "output_root"), base)
            self._sweep(root, days)
            items = [self._item(att, allow, deadline, root) for att in attachments]
        except (_BadParams, _PathRefused) as exc:
            return InvocationResult.failed(str(exc), retryable=False)
        except _StepFailed as exc:
            return InvocationResult.failed(str(exc), retryable=True)
        fetched = sum(1 for i in items if not i["skipped"])
        return InvocationResult.completed(
            {"items": items, "fetched": fetched, "skipped": len(items) - fetched}
        )

    @staticmethod
    def _params(input: Mapping[str, Any], config: Mapping[str, Any]) -> tuple[list, set[str]]:
        attachments = _param(input, config, "attachments")
        if not isinstance(attachments, list):
            raise _BadParams("attachments must be a list")
        hosts = _param(input, config, "allowed_hosts")
        if hosts is None:
            hosts = DEFAULT_ALLOWED_HOSTS
        if not isinstance(hosts, list | tuple):
            raise _BadParams("allowed_hosts must be a list of hostnames")
        allow = {_norm_host(h) for h in hosts if isinstance(h, str) and h.strip()}
        if any(_is_ip(h) for h in allow):
            raise _BadParams("allowed_hosts must be hostnames, not IP addresses")
        return attachments, allow

    def _sweep(self, root: Path, days: int) -> None:
        """Delete stored files (by name pattern, regular files only) older than ``days``."""
        cutoff = self._clock().timestamp() - days * 86400
        try:
            entries = list(os.scandir(root))
        except OSError as exc:
            raise _PathRefused("output_root cannot be listed") from exc
        for entry in entries:
            if not _STORED_NAME.fullmatch(entry.name) or entry.is_symlink():
                continue
            with contextlib.suppress(OSError):  # gone meanwhile, or not ours to delete
                if entry.is_file(follow_symlinks=False):
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        os.unlink(entry.path)

    def _item(self, att: Any, allow: set[str], deadline: datetime, root: Path) -> dict[str, Any]:
        filename = att.get("filename") if isinstance(att, Mapping) else None
        try:
            if not isinstance(att, Mapping) or not isinstance(att.get("url"), str):
                raise _Skip("invalid_item")
            fmt = _format(filename, att.get("content_type"))
            if fmt is None:
                raise _Skip("unsupported_format")
            if _over(att.get("size"), self._max_bytes):
                raise _Skip("too_large")
            sha256, size, path = self._fetch(att["url"], allow, deadline, root, fmt)
        except _Skip as skip:
            return {"filename": filename, "skipped": True, "reason": skip.reason}
        content_type = "application/pdf" if fmt == "pdf" else "application/epub+zip"
        return {
            "filename": filename,
            "skipped": False,
            "format": fmt,
            "content_type": content_type,
            "size": size,
            "sha256": sha256,
            "path": str(path),
        }

    # -- the vetted fetch (http.call's policy) -------------------------------------------

    def _pinned(self, url: str, allow: set[str]) -> tuple[str, str]:
        """(hostname, vetted address to dial) for ``url``, else :class:`_Skip`."""
        try:
            host, scheme, port = _checked_url(url)
        except _Refused as exc:
            reason = "host_refused" if exc.code == "destination_refused" else "invalid_item"
            raise _Skip(reason) from exc
        if scheme != "https" or host not in allow or _is_ip(host):
            raise _Skip("host_refused")
        try:
            addresses = [_norm_host(a) for a in self._resolver(host, port or _HTTPS_PORT)]
        except OSError as exc:
            raise _StepFailed("network_error: cannot resolve host") from exc
        if not addresses:
            raise _StepFailed("network_error: host resolved to no address")
        for address in addresses:
            try:
                _vet_address(host, address, set())  # no allow-list exception, ever
            except _Refused as exc:
                raise _Skip("private_address") from exc
        return host, addresses[0]

    def _fetch(
        self, url: str, allow: set[str], deadline: datetime, root: Path, fmt: str
    ) -> tuple[str, int, Path]:
        host, pinned = self._pinned(url, allow)
        timeout = (deadline - self._clock()).total_seconds()
        if timeout <= 0:
            raise _StepFailed("deadline_passed: attempt deadline already passed")
        try:
            request = urllib.request.Request(url, method="GET")
            request_host = urlsplit("//" + (request.host or "")).hostname or ""
        except ValueError as exc:
            raise _Skip("invalid_item") from exc
        if _norm_host(request_host) != host:  # a parser differential: refuse, as http.call does
            raise _Skip("host_refused")
        try:
            response = self._opener_factory(pinned).open(request, timeout=min(timeout, MAX_TIMEOUT))
        except RedirectRefused as exc:
            raise _Skip("redirect_refused") from exc
        except urllib.error.HTTPError as exc:
            response = exc  # non-2xx: an HTTPError is also the response
        except (http.client.HTTPException, OSError) as exc:  # URLError is an OSError
            raise _StepFailed(f"network_error: {type(exc).__name__}") from exc
        try:
            return self._store(response, root, fmt)
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()

    def _store(self, response: Any, root: Path, fmt: str) -> tuple[str, int, Path]:
        """Check the answer, stream its body into ``root`` and return (sha256, size, path)."""
        status = int(getattr(response, "status", None) or getattr(response, "code", 0))
        if 300 <= status < 400:
            raise _Skip("redirect_refused")
        if status >= 500 or status < 200 or status in _RETRYABLE_STATUS:
            raise _StepFailed(f"http_status: server answered {status}")
        if status >= 400:
            raise _Skip("http_status")
        if _over(_header(getattr(response, "headers", None), "content-length"), self._max_bytes):
            raise _Skip("too_large")
        fd, tmp = tempfile.mkstemp(dir=root, prefix=".attachment.", suffix=".part")
        try:
            try:
                with os.fdopen(fd, "wb") as out:
                    sha256, size = self._stream(response, out)
                return sha256, size, _place(tmp, root / f"{sha256}.{fmt}")
            except OSError as exc:  # a full disk, a file racing away: transient, classified
                raise _StepFailed(f"write_failed: {type(exc).__name__}") from exc
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _stream(self, response: Any, out: Any) -> tuple[str, int]:
        """Copy the body to ``out`` in chunks, refusing it once past the cap."""
        digest, size = hashlib.sha256(), 0
        while True:
            try:
                block = response.read(READ_CHUNK)
            except (OSError, http.client.HTTPException) as exc:
                raise _StepFailed(f"network_error: {type(exc).__name__}") from exc
            if not block:
                return digest.hexdigest(), size
            size += len(block)
            if size > self._max_bytes:
                raise _Skip("too_large")
            digest.update(block)
            out.write(block)


def _place(tmp: str, target: Path) -> Path:
    """Rename ``tmp`` to ``target`` (atomic); an existing regular file there is the same bytes
    and is reused (``tmp`` dropped, its age refreshed); anything else there is refused."""
    if os.path.lexists(target):
        if target.is_symlink() or not target.is_file():
            raise _PathRefused(f"{target.name} exists and is not a regular file")
        try:
            os.utime(target)
        except FileNotFoundError:  # removed meanwhile (retention): place ours instead
            os.replace(tmp, target)
            return target
        os.unlink(tmp)
        return target
    os.replace(tmp, target)
    return target
