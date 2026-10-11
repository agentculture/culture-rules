"""``llm.complete`` action port: one OpenAI-compatible chat completion, locked brief, no tools.

Params (``culture_rules.model.action_kinds``): ``actor`` (required; falls back to
``context.actor``), ``content`` (required: a string, sent verbatim, or any JSON-able value,
serialised deterministically with sorted keys), optional ``max_tokens`` (int >= 1, default
:data:`DEFAULT_MAX_TOKENS`, capped at :data:`MAX_TOKENS_CAP`) and ``temperature`` (a number
0..2). Every other param is ignored: a step cannot replace the brief, add tools or name a
repository.

The bound actor is an ``agent`` actor whose ``params`` carry ``base_url`` (``http(s)://host
[:port][/v1]``, no credentials), ``model`` (non-empty; else the actor's own ``model``) and
an optional ``api_key`` that must be a ``grant:<NAME>`` reference (resolved at call time on
this host and sent as a bearer token; a vLLM server may need none). The actor's
``concurrency_pool`` / ``max_concurrency`` are applied by
:class:`~culture_rules.node.actors.ActorRouter`, which wraps every action naming
``params.actor`` in a :class:`~culture_rules.actors.limits.LimitedActor` with that actor's
limits - a summary in pool ``qwen-spark2`` waits (``blocked``) behind a running fix. The
port never goes through the coding-agent bridge, even when the actor also has a
``bridge_url``.

The request is ``POST <base_url>/v1/chat/completions`` with exactly ``model``,
``messages`` (the system message is :data:`NEWSPAPER_BRIEF`, the user message the
content), ``max_tokens``, ``stream: false`` and, when given, ``temperature`` - never a
``tools`` field. Proxies from the environment are ignored and redirects are not followed.

Outcome: completed ``{text, model, usage}`` (``text`` is the first choice's message content
with a leading ``<think>`` block dropped; ``usage`` keeps the integer token counts, so the
actor's token budget counts them). Failures (``"<code>: <detail>"``, never a body, a header
value or a secret):

* ``llm_unavailable`` (retryable) - connection refused, DNS, timeout, a passed deadline,
  5xx, 408 or 429; the newspaper workflow sends without summaries on it;
* ``llm_rejected`` (not retryable) - any other non-2xx answer;
* ``llm_bad_response`` (not retryable) - not JSON, or no string message content;
* ``llm_content_too_large``, ``invalid_request``, ``llm_actor_invalid``,
  ``actor_kind_mismatch``, ``secret_unresolved`` (not retryable) - before any request.

Standard-library only.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.actors.secrets import GRANT_SCHEME, SecretError, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actions.machine import load_bound_actor

__all__ = [
    "DEFAULT_MAX_CONTENT_CHARS",
    "DEFAULT_MAX_TOKENS",
    "MAX_TOKENS_CAP",
    "NEWSPAPER_BRIEF",
    "LlmCompletePort",
    "Transport",
    "check_llm_actor",
    "urllib_transport",
]

NEWSPAPER_BRIEF = (
    "You write one section of a daily newspaper. The user message holds that section's web "
    "search results. Summarise only the given results: one bullet per item, each bullet a "
    "short summary that includes the item's link as a markdown link [title](url). Use no "
    "other sources and add nothing that is not in the results. If the list of results is "
    'empty, reply with exactly "No results." and nothing else. Write no preamble, no '
    "heading and no closing remarks: only the bullets."
)
"""The locked system prompt. It lives in code: no actor param or step param replaces it."""

DEFAULT_MAX_TOKENS = 1500
MAX_TOKENS_CAP = 4000
DEFAULT_MAX_CONTENT_CHARS = 48_000
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TIMEOUT = 600.0
COMPLETIONS_PATH = "/chat/completions"

UNAVAILABLE = "llm_unavailable"
REJECTED = "llm_rejected"
BAD_RESPONSE = "llm_bad_response"
INVALID = "invalid_request"
ACTOR_INVALID = "llm_actor_invalid"
_CONTENT, _MODEL = "content", "model"

_RETRYABLE_4XX = frozenset({408, 429})
_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")

Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, bytes]]
"""``(url, body, headers, timeout) -> (status, body)``; raises OSError /
``http.client.HTTPException`` when the server cannot be reached."""


class _Refused(Exception):
    def __init__(self, code: str, detail: str, *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}")
        self.retryable = retryable

    def result(self) -> InvocationResult:
        return InvocationResult.failed(str(self), retryable=self.retryable)


# ------------------------------------------------------------------ the default transport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_: Any, **__: Any) -> None:
        return None  # the 3xx then surfaces as an HTTPError: answered, not followed


def _opener() -> urllib.request.OpenerDirector:
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        urllib.request.HTTPDefaultErrorHandler(),
        _NoRedirect(),
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener  # no ProxyHandler: environment proxies are ignored


def urllib_transport(
    url: str, data: bytes, headers: Mapping[str, str], timeout: float
) -> tuple[int, bytes]:
    """POST ``data`` to ``url`` (http/https only, checked by the caller) with urllib."""
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        response = _opener().open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc  # a non-2xx answer is still an answer
    with response:
        status = int(getattr(response, "status", None) or response.code)
        return status, response.read(MAX_RESPONSE_BYTES + 1)


# ------------------------------------------------------------------ actor and input checks


def _base_url_problem(base_url: Any) -> str | None:
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


def check_llm_actor(actor: Actor) -> str | None:
    """Why ``actor`` cannot serve ``llm.complete`` (None when it can)."""
    params = actor.params
    problem = _base_url_problem(params.get("base_url"))
    if problem:
        return problem
    model = params.get(_MODEL) or actor.model
    if not isinstance(model, str) or not model.strip():
        return "params.model must be a non-empty string"
    key = params.get("api_key")
    if key not in (None, "") and not (isinstance(key, str) and key.startswith(GRANT_SCHEME + ":")):
        return "params.api_key must be a 'grant:<NAME>' reference"
    return None


def _completions_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    return base + COMPLETIONS_PATH if base.endswith("/v1") else base + "/v1" + COMPLETIONS_PATH


def _user_message(content: Any, limit: int) -> str:
    if isinstance(content, str):
        text = content
    elif content is None:
        text = ""
    else:
        try:
            text = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise _Refused(INVALID, "content is not JSON-serialisable") from exc
    if not text.strip():
        raise _Refused(INVALID, "content is required")
    if len(text) > limit:
        raise _Refused("llm_content_too_large", f"content is {len(text)} chars, limit {limit}")
    return text


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _max_tokens(value: Any) -> int:
    if value is None:
        return DEFAULT_MAX_TOKENS
    if not (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
        raise _Refused(INVALID, "max_tokens must be a positive integer")
    return min(value, MAX_TOKENS_CAP)


def _temperature(value: Any) -> float | None:
    if value is None:
        return None
    if not (_is_number(value) and 0 <= value <= 2):
        raise _Refused(INVALID, "temperature must be a number from 0 to 2")
    return float(value)


# ------------------------------------------------------------------ the response


def _parse(raw: bytes, fallback_model: str) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _Refused(BAD_RESPONSE, "response too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise _Refused(BAD_RESPONSE, "response is not JSON") from exc
    choices = data.get("choices") if isinstance(data, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    text = message.get(_CONTENT) if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise _Refused(BAD_RESPONSE, "no message content in the first choice")
    model = data.get(_MODEL)
    usage = data.get("usage")
    return {
        "text": _without_think(text),
        _MODEL: model if isinstance(model, str) and model else fallback_model,
        "usage": _usage(usage if isinstance(usage, dict) else {}),
    }


def _without_think(text: str) -> str:
    """``text`` without a leading ``<think>...</think>`` block (a reasoning model's notes)."""
    stripped = text.lstrip()
    if stripped.startswith(_THINK_OPEN) and _THINK_CLOSE in stripped:
        stripped = stripped.split(_THINK_CLOSE, 1)[1]
    return stripped.strip()


def _usage(usage: Mapping[str, Any]) -> dict[str, int]:
    return {
        k: v
        for k in _USAGE_KEYS
        if isinstance(v := usage.get(k), int) and not isinstance(v, bool) and v >= 0
    }


def _status_failure(status: int) -> _Refused:
    if status >= 500 or status in _RETRYABLE_4XX:
        return _Refused(UNAVAILABLE, f"server answered {status}", retryable=True)
    return _Refused(REJECTED, f"server answered {status}")


# ------------------------------------------------------------------ the port


class LlmCompletePort:
    """ActorPort for ``llm.complete`` (see the module docstring)."""

    # a completion has no side effect outside the model server, so repeating one after a
    # lost acknowledgement is safe; a completed key also replays from this process's ledger
    supports_idempotency_key = True

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secret_runner: Callable[[str], str] | None = None,
        max_content_chars: int = DEFAULT_MAX_CONTENT_CHARS,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport or urllib_transport
        self._secret_runner = secret_runner
        self._max_content_chars = max_content_chars
        self._clock = clock or (lambda: datetime.now(UTC))
        self._ledger: dict[str, InvocationResult] = {}

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if idempotency_key in self._ledger:
            return self._ledger[idempotency_key]
        actor, _doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            return failure or InvocationResult.failed("actor_not_found", retryable=False)
        try:
            result = self._complete(input, actor, deadline)
        except _Refused as exc:
            return exc.result()
        if result.outcome == COMPLETED:
            self._ledger[idempotency_key] = result
        return result

    def _request(self, input: Mapping[str, Any], model: str) -> bytes:
        body: dict[str, Any] = {
            _MODEL: model,
            "messages": [
                {"role": "system", _CONTENT: NEWSPAPER_BRIEF},
                {
                    "role": "user",
                    _CONTENT: _user_message(input.get(_CONTENT), self._max_content_chars),
                },
            ],
            "max_tokens": _max_tokens(input.get("max_tokens")),
            "stream": False,
        }
        temperature = _temperature(input.get("temperature"))
        if temperature is not None:
            body["temperature"] = temperature
        return json.dumps(body).encode("utf-8")

    def _headers(self, params: Mapping[str, Any]) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = params.get("api_key")
        if key:
            try:
                headers["Authorization"] = "Bearer " + resolve(key, self._secret_runner)
            except SecretError as exc:
                raise _Refused("secret_unresolved", "params.api_key") from exc
        return headers

    def _complete(
        self, input: Mapping[str, Any], actor: Actor, deadline: datetime
    ) -> InvocationResult:
        if actor.kind != "agent":
            raise _Refused(
                "actor_kind_mismatch",
                f"llm.complete needs an agent actor, {actor.id!r} is {actor.kind!r}",
            )
        problem = check_llm_actor(actor)
        if problem:
            raise _Refused(ACTOR_INVALID, f"{actor.id!r}: {problem}")
        model = str(actor.params.get(_MODEL) or actor.model).strip()
        data = self._request(input, model)
        timeout = (deadline - self._clock()).total_seconds()
        if timeout <= 0:
            raise _Refused(UNAVAILABLE, "attempt deadline already passed", retryable=True)
        headers = self._headers(actor.params)
        url = _completions_url(actor.params["base_url"])
        try:
            status, raw = self._transport(url, data, headers, min(timeout, MAX_TIMEOUT))
        except (OSError, http.client.HTTPException) as exc:  # URLError/timeouts are OSErrors
            raise _Refused(UNAVAILABLE, type(exc).__name__, retryable=True) from exc
        if not 200 <= status < 300:
            raise _status_failure(status)
        return InvocationResult.completed(_parse(raw, model))
