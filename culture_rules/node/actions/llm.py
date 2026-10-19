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

An optional ``not_after`` param (an ISO-8601 time with a UTC offset) is the latest time the
work may start: at or past it the port fails ``llm_deadline`` (not retryable) without a
request, and before it the request's timeout ends at it. Both are read on the clock *after*
the bearer token's grant lookup, just before the request (Codex r2 #2), so a lookup that
blocks past ``not_after`` sends nothing. A key that already completed in this process
(among its last :data:`LEDGER_SIZE` completions) replays its result
(:meth:`~LlmCompletePort.replay`) before any of this, also through
:class:`~culture_rules.actors.limits.LimitedActor`, which asks it before admission: a
completion re-asked after ``not_after`` replays, without a slot (Codex r2 #3). The ledger
is per process: after a node restart, or once evicted, a completion re-asked past its
``not_after`` fails ``llm_deadline`` - a missed summary, never a wrong send.
:class:`LlmCompletePort` also answers :meth:`~LlmCompletePort.refuse_before_admit`, which
:class:`~culture_rules.actors.limits.LimitedActor` asks before it queues the work, so a
summary waiting ``blocked`` behind a busy pool gives up at its next re-ask after
``not_after`` instead of waiting out the queue bound. The daily newspaper passes the
issue's ``summaries_until`` (the slot plus the summary window), so the issue goes out on
time with the results only (Codex r1 #3).

Outcome: completed ``{text, model, usage}`` (``text`` is the first choice's message content
with a leading ``<think>`` block dropped; ``usage`` keeps the integer token counts, so the
actor's token budget counts them). Failures (``"<code>: <detail>"``, never a body, a header
value or a secret):

* ``llm_unavailable`` (retryable) - connection refused, DNS, timeout, a passed deadline,
  5xx, 408 or 429. The newspaper's summary step tolerates any failure
  (``config.on_error: "skip"``): that section goes out with its results and "Summary
  unavailable.", and when no section got a summary the masthead also says so;
* ``llm_deadline`` (not retryable) - ``not_after`` has passed;
* ``llm_rejected`` (not retryable) - any other non-2xx answer;
* ``llm_bad_response`` (not retryable) - not JSON, or no string message content;
* ``model_taken`` (not retryable) - another enabled endpoint actor holds this actor's model
  (``model_taken: <holder id>``; checked before any request);
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

from culture_rules.actors.model_rule import model_holder
from culture_rules.actors.secrets import SecretError, is_secret_ref, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actions._ledger import DEFAULT_LEDGER_SIZE, ReplayLedger
from culture_rules.node.actions.machine import load_bound_actor
from culture_rules.node.actors import ACTORS_COLLECTION

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
LEDGER_SIZE = DEFAULT_LEDGER_SIZE
"""How many completed results the port keeps for :meth:`LlmCompletePort.replay`."""

UNAVAILABLE = "llm_unavailable"
REJECTED = "llm_rejected"
BAD_RESPONSE = "llm_bad_response"
INVALID = "invalid_request"
DEADLINE_PASSED = "llm_deadline"
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
    if key not in (None, "") and not is_secret_ref(key):
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


def _not_after(value: Any) -> datetime | None:
    if value is None:
        return None
    when = None
    if isinstance(value, str):
        try:
            when = datetime.fromisoformat(value)
        except ValueError:
            when = None
    if when is None or when.tzinfo is None:
        raise _Refused(INVALID, "not_after must be an ISO-8601 time with a UTC offset")
    return when


# ------------------------------------------------------------------ the response


def _parse(raw: bytes, fallback_model: str) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _Refused(BAD_RESPONSE, "response too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:  # UnicodeDecodeError is a ValueError
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
        self._ledger = ReplayLedger(LEDGER_SIZE)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        replayed = self.replay(idempotency_key)
        if replayed is not None:
            return replayed
        refused = self.refuse_before_admit(input)
        if refused is not None:
            return refused
        actor, _doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            return failure or InvocationResult.failed("actor_not_found", retryable=False)
        taken = self._model_taken(actor, _doc)
        if taken is not None:
            return taken
        try:
            result = self._complete(input, actor, deadline)
        except _Refused as exc:
            return exc.result()
        if result.outcome == COMPLETED:
            self._ledger.put(idempotency_key, result)
        return result

    def _model_taken(self, actor: Actor, doc: Mapping[str, Any] | None) -> InvocationResult | None:
        """``model_taken: <holder>`` when another enabled endpoint actor holds the model this
        request would use (``params.model``, else the actor's own ``model``)."""
        candidate = dict(doc or {})
        params = dict(candidate.get("params") or {})
        effective = params.get(_MODEL) or actor.model
        if isinstance(effective, str) and effective.strip():
            params[_MODEL] = effective.strip()
        candidate["params"] = params
        candidate.setdefault("id", actor.id)
        candidate.setdefault("kind", actor.kind)
        holder = model_holder(self._store.find(ACTORS_COLLECTION, {"kind": "agent"}), candidate)
        if holder is None:
            return None
        return InvocationResult.failed(f"model_taken: {holder}", retryable=False)

    def refuse_before_admit(self, input: Mapping[str, Any]) -> InvocationResult | None:
        """The failure for work that must not start (``not_after`` passed or malformed).

        :class:`~culture_rules.actors.limits.LimitedActor` asks this before it takes or waits
        for a slot, so a passed deadline is never queued."""
        try:
            self._not_after(input)
        except _Refused as exc:
            return exc.result()
        return None

    def _not_after(self, input: Mapping[str, Any]) -> datetime | None:
        when = _not_after(input.get("not_after"))
        if when is not None and self._clock() >= when:
            raise _Refused(DEADLINE_PASSED, "not_after has passed: the work may no longer start")
        return when

    def _remaining(self, input: Mapping[str, Any], deadline: datetime) -> float:
        """Seconds left for the request, read on the clock just before it is sent: a passed
        ``not_after`` fails ``llm_deadline``, a passed attempt deadline ``llm_unavailable``."""
        not_after = self._not_after(input)
        if not_after is not None:
            deadline = min(deadline, not_after)
        timeout = (deadline - self._clock()).total_seconds()
        if timeout <= 0:
            raise _Refused(UNAVAILABLE, "attempt deadline already passed", retryable=True)
        return timeout

    def replay(self, idempotency_key: str) -> InvocationResult | None:
        """The completed result already recorded for ``idempotency_key``, else None.

        :class:`~culture_rules.actors.limits.LimitedActor` asks this before
        :meth:`refuse_before_admit`, so a completion re-asked after ``not_after`` (its
        acknowledgement was lost) replays instead of failing ``llm_deadline`` (Codex r2 #3)."""
        return self._ledger.get(idempotency_key)

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
        headers = self._headers(actor.params)  # may block on a grant lookup (Codex r2 #2)
        url = _completions_url(actor.params["base_url"])
        timeout = self._remaining(input, deadline)
        try:
            status, raw = self._transport(url, data, headers, min(timeout, MAX_TIMEOUT))
        except (OSError, http.client.HTTPException) as exc:  # URLError/timeouts are OSErrors
            raise _Refused(UNAVAILABLE, type(exc).__name__, retryable=True) from exc
        if not 200 <= status < 300:
            raise _status_failure(status)
        return InvocationResult.completed(_parse(raw, model))
