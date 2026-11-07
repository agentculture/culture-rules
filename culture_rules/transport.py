"""The default HTTP transport the app clients share (standard library only).

``transport(method, url, headers, body, timeout) -> (status, body_bytes)``: one urllib
request that does not follow redirects, returns HTTP error statuses instead of raising, and
reads the whole response within one monotonic deadline and at most
:data:`MAX_RESPONSE_BYTES`. The GitHub, Jira, Resend and Brave clients default to it
(lifted from the GitHub client, where it was first written, in the Boundary release).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

__all__ = ["MAX_RESPONSE_BYTES", "Transport", "urllib_transport"]

Transport = Callable[[str, str, dict[str, str], bytes | None, float], tuple[int, bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def urllib_transport(
    method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    """Default transport: one urllib request; HTTP error statuses are returned, not raised."""
    if not url.startswith(("https://", "http://")):
        raise ValueError("unsupported url scheme")
    deadline_at = time.monotonic() + timeout  # the whole call: connect, send, full read
    req = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:  # nosec B310 - scheme checked above
            return resp.status, _read_within(resp, deadline_at)
    except urllib.error.HTTPError as exc:
        return exc.code, _read_within(exc, deadline_at)


MAX_RESPONSE_BYTES = 8 << 20
"""The largest response body read (d26: a listing of a long PR's comments is far less)."""
_CHUNK = 64 << 10


def _read_within(stream: Any, deadline_at: float, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read ``stream`` to its end in chunks, within the monotonic ``deadline_at`` (a
    response trickling in slower than the socket timeout raises ``TimeoutError``, which the
    App reports as ``deadline_exceeded`` under :meth:`GitHubApp.deadline`) and at most
    ``max_bytes`` (``ValueError``)."""
    chunks: list[bytes] = []
    size = 0
    while True:
        if time.monotonic() >= deadline_at:
            raise TimeoutError("the response did not arrive within the call's deadline")
        chunk = stream.read1(_CHUNK) if hasattr(stream, "read1") else stream.read(_CHUNK)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > max_bytes:
            raise ValueError("response too large")
        chunks.append(chunk)
