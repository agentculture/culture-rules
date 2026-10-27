"""``llm.complete`` action port: one OpenAI-compatible chat completion, locked brief, no tools.

Params (``culture_rules.model.action_kinds``): ``actor`` (required; falls back to
``context.actor``), ``content`` (a string, sent verbatim, or any JSON-able value,
serialised deterministically with sorted keys) or, instead, ``content_path`` with
``content_root`` and optional ``content_max_chars`` (d2: the text of the schema-1 transcript at
``content_path``, which must resolve under ``content_root``, symlinks followed, else
``path_refused``; cut to ``content_max_chars``, at most the content limit, so a long transcript
is summarised from its start instead of refused; the text never enters the run's outputs),
optional ``max_tokens`` (int >= 1, default
:data:`DEFAULT_MAX_TOKENS`, capped at :data:`MAX_TOKENS_CAP`) and ``temperature`` (a number
0..2), and optional ``brief``: absent or ``"newspaper"`` selects :data:`NEWSPAPER_BRIEF`,
``"transcript"`` selects :data:`TRANSCRIPT_BRIEF`, ``"recording"`` selects
:data:`RECORDING_BRIEF`, anything else (including null) fails
``invalid_request`` before any request. Every other param is ignored: a step cannot
replace the brief, add tools or name a repository.

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
``messages`` (the system message is the chosen locked brief, the user message the
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

Answer cache (``brief: "recording"`` only): with the param ``cache: true`` and a ``content_path``,
the parsed answer is kept beside the content file as
``answer.recording.<first 16 hex of the content file's sha256>.json`` (written atomically,
like ``transcript.render``'s book) and a later run over the same file bytes returns it without
calling the model (``cached: true``). Only a parsed, valid answer is stored; a cache file that
does not check out is ignored and rewritten; a folder that cannot be written still returns the
answer. ``cache`` is a boolean, and only the recording brief has a parser, so ``cache: true``
with another brief or without ``content_path`` fails ``invalid_request`` before any request.
The recording brief's reply is parsed by
:func:`~culture_rules.node.actions.recording_answer.parse_recording_answer`; a reply that does
not qualify fails ``answer_not_json`` / ``answer_invalid`` (not retryable).

Outcome: completed ``{text, model, usage}`` (``text`` is the first choice's message content
with a leading ``<think>`` block dropped; ``usage`` keeps the integer token counts, so the
actor's token budget counts them; the recording brief also returns ``answer``, the parsed
``{summary: [lines], word, facts, action_items}``, and ``cached``). Failures
(``"<code>: <detail>"``, never a body, a header value or a secret):

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

import contextlib
import http.client
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from culture_rules.actors.secrets import SecretError, is_secret_ref, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actions._endpoint import (
    INVALID,
    Refused,
    base_url_problem,
    check_not_after,
    is_number,
    model_taken,
    post,
    time_left,
)
from culture_rules.node.actions._ledger import DEFAULT_LEDGER_SIZE, ReplayLedger
from culture_rules.node.actions.machine import load_bound_actor
from culture_rules.node.actions.recording_answer import AnswerRefused, parse_recording_answer
from culture_rules.node.actions.transcript_file import (
    ExcerptRefused,
    _under,
    file_sha256,
    read_excerpt,
    write_bytes_atomic,
)

__all__ = [
    "DEFAULT_MAX_CONTENT_CHARS",
    "DEFAULT_MAX_TOKENS",
    "MAX_TOKENS_CAP",
    "NEWSPAPER_BRIEF",
    "RECORDING_BRIEF",
    "TRANSCRIPT_BRIEF",
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

TRANSCRIPT_BRIEF = (
    "You summarise one audio recording from its transcript. The user message holds the "
    "transcript text, often in Hebrew. Reply with only a JSON object of exactly this shape: "
    '{"summary": "...", "word": "..."}. "summary" is one or two short lines, in the same '
    'language as the transcript, saying what the recording is about. "word" is a single '
    "short English word, in Latin letters (letters and hyphens only), naming its topic. Use "
    "only what the transcript says. Write no other text, no markdown fence and no preamble."
)
"""The locked system prompt for ``brief: "transcript"``. Also code-only, never built from params."""

RECORDING_BRIEF = (
    "You digest one audio recording from its transcript. The user message holds the "
    "transcript text, in any language. Reply with only a JSON object of exactly this shape: "
    '{"summary": "...", "word": "...", "facts": ["..."], "action_items": ["..."]}. '
    '"summary" is one or two short lines, in the same language as the transcript, saying '
    'what the recording is about. "word" is a single short English word, in Latin letters '
    '(letters and hyphens only), naming its topic. "facts" is a list of short key facts the '
    'recording states. "action_items" is a list of short imperative to-dos that the '
    "recording actually states or agrees on. Use an empty list when there are none, and "
    "never invent a fact or a to-do. Use only what the transcript says. Write no other text, "
    "no markdown fence and no preamble."
)
"""The locked system prompt for ``brief: "recording"``. Code-only, never built from params."""

_BRIEFS = {
    "newspaper": NEWSPAPER_BRIEF,
    "transcript": TRANSCRIPT_BRIEF,
    "recording": RECORDING_BRIEF,
}
_CACHE = "cache"
_CACHE_BRIEF = "recording"
_CACHE_SCHEMA = 1

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
DEADLINE_PASSED = "llm_deadline"
ACTOR_INVALID = "llm_actor_invalid"
_CONTENT, _MODEL = "content", "model"
_CONTENT_PATH, _CONTENT_ROOT, _CONTENT_MAX_CHARS = (
    "content_path",
    "content_root",
    "content_max_chars",
)

_RETRYABLE_4XX = frozenset({408, 429})
_THINK_OPEN, _THINK_CLOSE = "<think>", "</think>"
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")

Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, bytes]]
"""``(url, body, headers, timeout) -> (status, body)``; raises OSError /
``http.client.HTTPException`` when the server cannot be reached."""


# ------------------------------------------------------------------ the default transport


def urllib_transport(
    url: str, data: bytes, headers: Mapping[str, str], timeout: float
) -> tuple[int, bytes]:
    """POST ``data`` to ``url`` (http/https only, checked by the caller) with urllib."""
    status, _headers, body = post(url, data, headers, timeout, MAX_RESPONSE_BYTES)
    return status, body


# ------------------------------------------------------------------ actor and input checks


def check_llm_actor(actor: Actor) -> str | None:
    """Why ``actor`` cannot serve ``llm.complete`` (None when it can)."""
    params = actor.params
    problem = base_url_problem(params.get("base_url"))
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
            raise Refused(INVALID, "content is not JSON-serialisable") from exc
    if not text.strip():
        raise Refused(INVALID, "content is required")
    if len(text) > limit:
        raise Refused("llm_content_too_large", f"content is {len(text)} chars, limit {limit}")
    return text


def _brief(input: Mapping[str, Any]) -> str:
    if "brief" not in input:
        return NEWSPAPER_BRIEF
    name = input["brief"]
    if not isinstance(name, str) or name not in _BRIEFS:
        raise Refused(INVALID, "brief must be 'newspaper', 'transcript' or 'recording'")
    return _BRIEFS[name]


def _max_tokens(value: Any) -> int:
    if value is None:
        return DEFAULT_MAX_TOKENS
    if not (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
        raise Refused(INVALID, "max_tokens must be a positive integer")
    return min(value, MAX_TOKENS_CAP)


def _temperature(value: Any) -> float | None:
    if value is None:
        return None
    if not (is_number(value) and 0 <= value <= 2):
        raise Refused(INVALID, "temperature must be a number from 0 to 2")
    return float(value)


# ------------------------------------------------------------------ the response


def _parse(raw: bytes, fallback_model: str) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise Refused(BAD_RESPONSE, "response too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:  # UnicodeDecodeError is a ValueError
        raise Refused(BAD_RESPONSE, "response is not JSON") from exc
    choices = data.get("choices") if isinstance(data, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else None
    message = first.get("message") if isinstance(first, dict) else None
    text = message.get(_CONTENT) if isinstance(message, dict) else None
    if not isinstance(text, str):
        raise Refused(BAD_RESPONSE, "no message content in the first choice")
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


def _cache_path(input: Mapping[str, Any]) -> tuple[Path, str] | None:
    """``(answer cache file, content sha256)`` when ``cache: true`` applies, else None."""
    flag = input.get(_CACHE)
    if flag is None or flag is False:
        return None
    if flag is not True:
        raise Refused(INVALID, "cache must be a boolean")
    if input.get("brief") != _CACHE_BRIEF or input.get(_CONTENT_PATH) is None:
        raise Refused(INVALID, "cache needs brief 'recording' and a content_path")
    try:
        path = _under(input.get(_CONTENT_PATH), input.get(_CONTENT_ROOT))
        digest = file_sha256(path)
    except ExcerptRefused as exc:
        raise Refused(exc.code, exc.detail) from exc
    except OSError as exc:
        raise Refused(INVALID, "the content file is not readable") from exc
    return path.parent / f"answer.{_CACHE_BRIEF}.{digest[:16]}.json", digest


def _cached_answer(file: Path, digest: str) -> dict[str, Any] | None:
    """The outputs stored for exactly this content file, or None (a damaged file is ignored)."""
    try:
        doc = json.loads(file.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or doc.get("content_sha256") != digest:
            return None
        if doc.get("schema") != _CACHE_SCHEMA or doc.get("brief") != _CACHE_BRIEF:
            return None
        stored = doc.get("answer")
        if not isinstance(stored, dict) or not isinstance(stored.get("summary"), list):
            return None
        answer = parse_recording_answer(
            json.dumps({**stored, "summary": "\n".join(map(str, stored["summary"]))})
        )
        model, usage = doc.get(_MODEL), doc.get("usage")
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(model, str) or not model or not isinstance(usage, dict):
        return None
    return {
        "text": json.dumps(answer, sort_keys=True, ensure_ascii=False),
        _MODEL: model,
        "usage": {},  # no model call, so nothing counts toward the actor's token budget
        "answer": answer,
        "cached": True,
    }


def _store_answer(file: Path, digest: str, out: Mapping[str, Any]) -> None:
    """Keep a parsed answer beside its content file (best effort: an unwritable folder only
    means the model is asked again next time)."""
    doc = {
        "schema": _CACHE_SCHEMA,
        "brief": _CACHE_BRIEF,
        "content_sha256": digest,
        _MODEL: out[_MODEL],
        "usage": out["usage"],
        "answer": out["answer"],
    }
    with contextlib.suppress(OSError):
        write_bytes_atomic(
            file, json.dumps(doc, sort_keys=True, ensure_ascii=False).encode("utf-8")
        )


def _status_failure(status: int) -> Refused:
    if status >= 500 or status in _RETRYABLE_4XX:
        return Refused(UNAVAILABLE, f"server answered {status}", retryable=True)
    return Refused(REJECTED, f"server answered {status}")


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
        taken = model_taken(self._store, actor, _doc)
        if taken is not None:
            return taken.result()
        try:
            result = self._complete(input, actor, deadline)
        except Refused as exc:
            return exc.result()
        if result.outcome == COMPLETED:
            self._ledger.put(idempotency_key, result)
        return result

    def refuse_before_admit(self, input: Mapping[str, Any]) -> InvocationResult | None:
        """The failure for work that must not start (``not_after`` passed or malformed).

        :class:`~culture_rules.actors.limits.LimitedActor` asks this before it takes or waits
        for a slot, so a passed deadline is never queued."""
        try:
            self._not_after(input)
        except Refused as exc:
            return exc.result()
        return None

    def _not_after(self, input: Mapping[str, Any]) -> datetime | None:
        return check_not_after(input, self._clock, DEADLINE_PASSED)

    def _remaining(self, input: Mapping[str, Any], deadline: datetime) -> float:
        """Seconds left for the request, read on the clock just before it is sent: a passed
        ``not_after`` fails ``llm_deadline``, a passed attempt deadline ``llm_unavailable``."""
        return time_left(deadline, self._clock, UNAVAILABLE, self._not_after(input))

    def replay(self, idempotency_key: str) -> InvocationResult | None:
        """The completed result already recorded for ``idempotency_key``, else None.

        :class:`~culture_rules.actors.limits.LimitedActor` asks this before
        :meth:`refuse_before_admit`, so a completion re-asked after ``not_after`` (its
        acknowledgement was lost) replays instead of failing ``llm_deadline`` (Codex r2 #3)."""
        return self._ledger.get(idempotency_key)

    def _user_content(self, input: Mapping[str, Any]) -> str:
        """``content``, or with ``content_path`` the transcript's text read under
        ``content_root`` and cut to ``content_max_chars`` (d2: it never enters run state)."""
        if input.get(_CONTENT_PATH) is None:
            return _user_message(input.get(_CONTENT), self._max_content_chars)
        if input.get(_CONTENT) is not None:
            raise Refused(INVALID, "give content or content_path, not both")
        limit = input.get(_CONTENT_MAX_CHARS, self._max_content_chars)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise Refused(INVALID, "content_max_chars must be an integer >= 1")
        try:
            text = read_excerpt(
                input.get(_CONTENT_PATH),
                input.get(_CONTENT_ROOT),
                min(limit, self._max_content_chars),
            )
        except ExcerptRefused as exc:
            raise Refused(exc.code, exc.detail) from exc
        if not text:
            raise Refused(INVALID, "the transcript has no text to send")
        return text

    def _request(self, input: Mapping[str, Any], model: str, content: str) -> bytes:
        body: dict[str, Any] = {
            _MODEL: model,
            "messages": [
                {"role": "system", _CONTENT: _brief(input)},
                {"role": "user", _CONTENT: content},
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
                raise Refused("secret_unresolved", "params.api_key") from exc
        return headers

    def _complete(
        self, input: Mapping[str, Any], actor: Actor, deadline: datetime
    ) -> InvocationResult:
        if actor.kind != "agent":
            raise Refused(
                "actor_kind_mismatch",
                f"llm.complete needs an agent actor, {actor.id!r} is {actor.kind!r}",
            )
        problem = check_llm_actor(actor)
        if problem:
            raise Refused(ACTOR_INVALID, f"{actor.id!r}: {problem}")
        model = str(actor.params.get(_MODEL) or actor.model).strip()
        cache = _cache_path(input)
        if cache is not None:
            hit = _cached_answer(*cache)
            if hit is not None:
                return InvocationResult.completed(hit)
        _brief(input)  # a bad brief is refused before the content is read
        data = self._request(input, model, self._user_content(input))
        headers = self._headers(actor.params)  # may block on a grant lookup (Codex r2 #2)
        url = _completions_url(actor.params["base_url"])
        timeout = self._remaining(input, deadline)
        try:
            status, raw = self._transport(url, data, headers, min(timeout, MAX_TIMEOUT))
        except (OSError, http.client.HTTPException) as exc:  # URLError/timeouts are OSErrors
            raise Refused(UNAVAILABLE, type(exc).__name__, retryable=True) from exc
        if not 200 <= status < 300:
            raise _status_failure(status)
        out = _parse(raw, model)
        if input.get("brief") == _CACHE_BRIEF:
            try:
                out["answer"] = parse_recording_answer(out["text"])
            except AnswerRefused as exc:
                raise Refused(exc.code, exc.detail) from exc
            out["cached"] = False
            if cache is not None:
                _store_answer(*cache, out)
        return InvocationResult.completed(out)
