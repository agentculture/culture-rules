"""What the endpoint-actor ports share (#87): one copy, imported by all three.

``llm.complete`` (:mod:`~culture_rules.node.actions.llm`), ``audio.transcribe``
(:mod:`~culture_rules.node.actions.stt`) and ``decider.assess``
(:mod:`~culture_rules.node.actions.decider`) each POST to an agent actor's ``base_url``. They
share the transport (no environment proxies, no redirects followed), the ``base_url`` check,
the ``not_after`` and attempt-deadline arithmetic, and the model rule. Failure codes stay each
port's own (``llm_unavailable``, ``stt_unavailable``, ...), so the helpers take them as
arguments. Standard-library only.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.actors.model_rule import senior_holder
from culture_rules.engine.actorport import InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = [
    "INVALID",
    "NoRedirect",
    "Refused",
    "base_url_problem",
    "check_not_after",
    "is_number",
    "model_taken",
    "opener",
    "parse_not_after",
    "post",
    "time_left",
]

INVALID = "invalid_request"


class Refused(Exception):
    """A port's failure, raised inside the work and turned into its result at the edge."""

    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}")
        self.retryable = retryable

    def result(self) -> InvocationResult:
        return InvocationResult.failed(str(self), retryable=self.retryable)


# ------------------------------------------------------------------ the default transport


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None  # the 3xx then surfaces as an HTTPError: answered, not followed


def opener() -> urllib.request.OpenerDirector:
    director = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        NoRedirect(),
        urllib.request.HTTPErrorProcessor(),
    ):
        director.add_handler(handler)
    return director  # no ProxyHandler: environment proxies are ignored


def post(
    url: str, data: bytes, headers: Mapping[str, str], timeout: float, max_bytes: int
) -> tuple[int, dict[str, str], bytes]:
    """POST ``data`` to ``url`` (http/https only, checked by the caller) with urllib: the
    status, the reply headers and at most ``max_bytes + 1`` bytes of the body (one more than
    the cap, so the caller can tell an oversized reply)."""
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        response = opener().open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc  # a non-2xx answer is still an answer
    with response:
        status = int(getattr(response, "status", None) or response.code)
        return status, dict(response.headers.items()), response.read(max_bytes + 1)


# ------------------------------------------------------------------ actor and input checks


def base_url_problem(base_url: Any) -> str | None:
    if not isinstance(base_url, str) or not base_url.strip():
        return "params.base_url must be an http(s) URL"
    try:
        parts = urlsplit(base_url.strip())
        _ = parts.port  # raises on a malformed port
    except ValueError:
        return "params.base_url is malformed"
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return "params.base_url must be an http(s) URL with a host"
    if parts.username is not None or parts.password is not None:
        return "params.base_url must not carry credentials"
    if parts.query or parts.fragment:
        return "params.base_url must not carry a query or fragment"
    return None


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def model_taken(store: Any, actor: Actor, doc: Mapping[str, Any] | None) -> Refused | None:
    """``model_taken: <holder>`` when another enabled endpoint actor with a lower id holds
    the model this request would use (``params.model``, else the actor's own ``model``).
    Of two actors sharing a model, the lowest id keeps serving."""
    candidate = dict(doc or {})
    candidate.setdefault("id", actor.id)
    candidate.setdefault("kind", actor.kind)
    candidate.setdefault("model", actor.model)
    holder = senior_holder(store.find(ACTORS_COLLECTION, {"kind": "agent"}), candidate)
    if holder is None:
        return None
    return Refused("model_taken", str(holder))


# ------------------------------------------------------------------ not_after and deadlines


def parse_not_after(value: Any) -> datetime | None:
    if value is None:
        return None
    when = None
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            when = None
    if when is None or when.tzinfo is None:
        raise Refused(INVALID, "not_after must be an ISO-8601 time with a UTC offset")
    return when


def check_not_after(
    input: Mapping[str, Any], clock: Callable[[], datetime], code: str
) -> datetime | None:
    """The input's ``not_after``; ``code`` (the port's deadline code) once it has passed.
    The clock is read only when there is a ``not_after``."""
    when = parse_not_after(input.get("not_after"))
    if when is not None and clock() >= when:
        raise Refused(code, "not_after has passed: the work may no longer start")
    return when


def time_left(
    deadline: datetime,
    clock: Callable[[], datetime],
    code: str,
    not_after: datetime | None = None,
) -> float:
    """Seconds left before the attempt ``deadline`` (or an earlier ``not_after``), read on the
    clock just before a request is sent; ``code`` (retryable) when none are left."""
    if not_after is not None:
        deadline = min(deadline, not_after)
    timeout = (deadline - clock()).total_seconds()
    if timeout <= 0:
        raise Refused(code, "attempt deadline already passed", retryable=True)
    return timeout
