"""``decider.assess`` action port: one assessment from the mesh's decider, no other effect.

The decider (the lobes gateway's ``POST /v1/systemone``) reads a situation and returns a
probability for every option of every question it is asked. It never writes text and never
acts, so this port is a pure read: one request per invocation, safe to repeat.

Params (``culture_rules.model.action_kinds`` registers them, t2): ``actor`` (required; falls
back to ``context.actor``), ``state`` (a non-empty string of facts), ``questions`` (a non-empty
mapping ``key -> question``) and an optional ``not_after``. Every other param is ignored: the
model is the constant ``clef`` and the domain is the *actor's* (an operator decision), so a step
cannot change either. A step that carries ``images`` or ``videos`` (or a question that does) is
refused: the decider is text-only.

A question key matches ``^[a-z][a-z0-9_]*$``; a question is ``{type, instructions, criteria}``
with ``type`` one of ``choice`` (pick one option), ``noul`` (a yes/no likelihood) or ``score``
(an ordinal scale, lowest first). ``choice`` and ``score`` need ``criteria``: a mapping of at
least two ``key -> description`` texts. Only those three fields are sent.

The bound actor is an ``agent`` actor whose ``params`` carry ``base_url``
(``http(s)://host[:port][/v1]``, no credentials), ``model`` (non-empty, else the actor's own
``model``; it is the model rule's key, never sent), an optional ``api_key`` that must be a
``grant:<NAME>`` reference (resolved at call time, sent as a bearer token) and an optional
``domain`` (``^[a-z0-9][a-z0-9-]*$``, default ``general``).

The request is ``POST <base_url>/v1/systemone`` (a ``/v1`` already on ``base_url`` is not
doubled) with exactly ``{"model": "clef", "state", "questions"}`` and the header
``X-Lobes-Model: decider:<domain>``. Proxies from the environment are ignored and redirects
are not followed. The serialised request may not exceed :data:`MAX_REQUEST_CHARS` characters
(the decider's window is 16384 tokens; the cap leaves room for the answer).

An optional ``not_after`` (ISO-8601 with a UTC offset) behaves as in ``llm.complete``: at or
past it the port fails ``decider_deadline`` without a request (read on the clock after the
grant lookup, just before sending); a completed key replays its result
(:meth:`~DeciderAssessPort.replay`) even after ``not_after``;
:meth:`~DeciderAssessPort.refuse_before_admit` lets the limits wrapper give up before queuing.

Outcome: completed ``{answers, picks, host, domain, usage}``:

* ``answers`` - the reply's ``answers`` mapping exactly as replied;
* ``picks`` - per question: ``choice`` -> the chosen option key; ``noul`` -> ``noul >= 0.5``;
  ``score`` -> the legend label of the most probable index (ties to the lower index);
* ``host`` - the ``X-Lobes-Mesh-Member`` reply header, else ``"local"``;
* ``domain`` - the actor's domain;
* ``usage`` - integer ``{input_tokens, output_tokens, total_tokens}``.

Failures (``"<code>: <detail>"``; never the state, a reply body, a header value or a secret):

* ``decider_unavailable`` (retryable) - network error, timeout, a passed attempt deadline,
  5xx, 408 or 429;
* ``decider_deadline`` (not retryable) - ``not_after`` has passed;
* ``decider_rejected`` (not retryable) - any other non-2xx answer, with its status and the
  reply's short error code when it carries one;
* ``decider_bad_response`` (not retryable) - not JSON, no ``answers``, or a question without
  its fields; never partial picks;
* ``model_taken`` (not retryable) - a senior enabled endpoint actor holds the model;
* ``invalid_request``, ``decider_actor_invalid``, ``actor_kind_mismatch``,
  ``secret_unresolved`` (not retryable) - before any request.

Standard-library only. The port never logs.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from culture_rules.actors.model_rule import senior_holder
from culture_rules.actors.secrets import SecretError, is_secret_ref, resolve
from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.model.actor import Actor
from culture_rules.node.actions._ledger import DEFAULT_LEDGER_SIZE, ReplayLedger
from culture_rules.node.actions.machine import load_bound_actor
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = [
    "DEFAULT_DOMAIN",
    "MAX_REQUEST_CHARS",
    "DeciderAssessPort",
    "Transport",
    "check_decider_actor",
    "urllib_transport",
]

REQUEST_MODEL = "clef"
DEFAULT_DOMAIN = "general"
SYSTEMONE_PATH = "/systemone"
MAX_REQUEST_CHARS = 48_000
"""The cap on the serialised request: the decider's window is 16384 tokens (about 4 chars each
in English, fewer in Hebrew), so a request this long is refused instead of silently cut."""
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_TIMEOUT = 120.0
LEDGER_SIZE = DEFAULT_LEDGER_SIZE
"""How many completed results the port keeps for :meth:`DeciderAssessPort.replay`."""

UNAVAILABLE = "decider_unavailable"
REJECTED = "decider_rejected"
BAD_RESPONSE = "decider_bad_response"
DEADLINE_PASSED = "decider_deadline"
ACTOR_INVALID = "decider_actor_invalid"
INVALID = "invalid_request"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_CODE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_TYPES = ("choice", "noul", "score")
_NEEDS_CRITERIA = ("choice", "score")
_MEDIA_KEYS = ("images", "videos")
_RETRYABLE_4XX = frozenset({408, 429})
_MESH_HEADER = "x-lobes-mesh-member"

Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, Mapping[str, str], bytes]]
"""``(url, body, headers, timeout) -> (status, headers, body)``; raises OSError /
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
) -> tuple[int, Mapping[str, str], bytes]:
    """POST ``data`` to ``url`` (http/https only, checked by the caller) with urllib."""
    request = urllib.request.Request(url, data=data, headers=dict(headers), method="POST")
    try:
        response = _opener().open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        response = exc  # a non-2xx answer is still an answer
    with response:
        status = int(getattr(response, "status", None) or response.code)
        return status, dict(response.headers.items()), response.read(MAX_RESPONSE_BYTES + 1)


# ------------------------------------------------------------------ the actor


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


def _domain(params: Mapping[str, Any]) -> str | None:
    """The actor's domain, or None when it is malformed."""
    value = params.get("domain", DEFAULT_DOMAIN)
    if isinstance(value, str) and _DOMAIN_RE.match(value):
        return value
    return None


def check_decider_actor(actor: Actor) -> str | None:
    """Why ``actor`` cannot serve ``decider.assess`` (None when it can)."""
    params = actor.params
    problem = _base_url_problem(params.get("base_url"))
    if problem:
        return problem
    model = params.get("model") or actor.model
    if not isinstance(model, str) or not model.strip():
        return "params.model must be a non-empty string"
    key = params.get("api_key")
    if key not in (None, "") and not is_secret_ref(key):
        return "params.api_key must be a 'grant:<NAME>' reference"
    if _domain(params) is None:
        return "params.domain must be lowercase letters, digits and hyphens"
    return None


def _systemone_url(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    return base + SYSTEMONE_PATH if base.endswith("/v1") else base + "/v1" + SYSTEMONE_PATH


# ------------------------------------------------------------------ the request


def _is_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _question(key: Any, raw: Any) -> dict[str, Any]:
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise _Refused(INVALID, "a question key must match ^[a-z][a-z0-9_]*$")
    if not isinstance(raw, Mapping):
        raise _Refused(INVALID, f"question {key!r} must be an object")
    if any(raw.get(m) for m in _MEDIA_KEYS):
        raise _Refused(INVALID, f"question {key!r}: images and videos are not supported")
    kind = raw.get("type")
    if kind not in _TYPES:
        raise _Refused(INVALID, f"question {key!r}: type must be choice, noul or score")
    if not _is_text(raw.get("instructions")):
        raise _Refused(INVALID, f"question {key!r}: instructions are required")
    question: dict[str, Any] = {"type": kind, "instructions": raw["instructions"]}
    if kind in _NEEDS_CRITERIA:
        criteria = raw.get("criteria")
        ok = isinstance(criteria, Mapping) and len(criteria) >= 2
        if not ok or not all(_is_text(k) and _is_text(v) for k, v in criteria.items()):
            raise _Refused(INVALID, f"question {key!r}: needs at least two text criteria")
        question["criteria"] = dict(criteria)
    return question


def _build_request(input: Mapping[str, Any]) -> bytes:
    if any(input.get(m) for m in _MEDIA_KEYS):
        raise _Refused(INVALID, "images and videos are not supported")
    state = input.get("state")
    if not _is_text(state):
        raise _Refused(INVALID, "state must be a non-empty string")
    raw = input.get("questions")
    if not isinstance(raw, Mapping) or not raw:
        raise _Refused(INVALID, "questions must be a non-empty object")
    questions = {key: _question(key, value) for key, value in raw.items()}
    body = {"model": REQUEST_MODEL, "state": state, "questions": questions}
    try:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _Refused(INVALID, "the request is not JSON-serialisable") from exc
    if len(data.decode("utf-8")) > MAX_REQUEST_CHARS:
        raise _Refused(INVALID, f"the request is over {MAX_REQUEST_CHARS} characters")
    return data


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


def _bad(detail: str) -> _Refused:
    return _Refused(BAD_RESPONSE, detail)


def _json_object(raw: bytes) -> dict[str, Any]:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _bad("response too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as exc:  # UnicodeDecodeError is a ValueError
        raise _bad("response is not JSON") from exc
    if not isinstance(data, dict):
        raise _bad("response is not an object")
    return data


def _probability(value: Any) -> bool:
    return _is_number(value) and 0 <= value <= 1


def _pick_choice(key: str, answer: Mapping[str, Any], asked: Mapping[str, Any]) -> str:
    choice = answer.get("choice")
    if not isinstance(choice, str) or choice not in asked["criteria"]:
        raise _bad(f"{key}: no valid choice")
    return choice


def _pick_noul(key: str, answer: Mapping[str, Any], asked: Mapping[str, Any]) -> bool:
    value = answer.get("noul")
    if not _probability(value):
        raise _bad(f"{key}: no valid noul")
    return value >= 0.5


def _pick_score(key: str, answer: Mapping[str, Any], asked: Mapping[str, Any]) -> str:
    probs, legend = answer.get("probabilities"), answer.get("legend")
    if not isinstance(probs, dict) or not isinstance(legend, dict) or not probs:
        raise _bad(f"{key}: no probabilities or legend")
    if set(probs) != set(legend) or len(legend) != len(asked["criteria"]):
        raise _bad(f"{key}: legend does not match the criteria")
    try:
        scored = [(int(index), p) for index, p in probs.items()]
    except (TypeError, ValueError) as exc:
        raise _bad(f"{key}: non-numeric score index") from exc
    if not all(_probability(p) for _, p in scored):
        raise _bad(f"{key}: invalid probability")
    best = min(scored, key=lambda item: (-item[1], item[0]))[0]
    label = next((v for k, v in legend.items() if int(k) == best), None)
    if not isinstance(label, str):
        raise _bad(f"{key}: legend has no label for the pick")
    return label


_PICKERS = {"choice": _pick_choice, "noul": _pick_noul, "score": _pick_score}


def _picks(answers: Any, questions: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(answers, dict):
        raise _bad("no answers")
    picks: dict[str, Any] = {}
    for key, asked in questions.items():
        answer = answers.get(key)
        if not isinstance(answer, dict) or answer.get("type") != asked["type"]:
            raise _bad(f"{key}: answer missing or of the wrong type")
        picks[key] = _PICKERS[asked["type"]](key, answer, asked)
    return picks


def _count(usage: Any, key: str) -> int:
    value = usage.get(key) if isinstance(usage, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _usage(usage: Any) -> dict[str, int]:
    tokens_in, tokens_out = _count(usage, "input_tokens"), _count(usage, "output_tokens")
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "total_tokens": tokens_in + tokens_out,
    }


def _host(headers: Mapping[str, str]) -> str:
    for name, value in headers.items():
        if str(name).lower() == _MESH_HEADER and isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return "local"


def _error_code(raw: bytes) -> str | None:
    """The reply's short error code (``{"error": {"code": ...}}``), never its detail text."""
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and _CODE_RE.match(code) else None


def _status_failure(status: int, raw: bytes) -> _Refused:
    if status >= 500 or status in _RETRYABLE_4XX:
        return _Refused(UNAVAILABLE, f"server answered {status}", retryable=True)
    code = _error_code(raw)
    return _Refused(REJECTED, f"status {status}" + (f" ({code})" if code else ""))


# ------------------------------------------------------------------ the port


class DeciderAssessPort:
    """ActorPort for ``decider.assess`` (see the module docstring)."""

    # an assessment has no side effect, so repeating one after a lost acknowledgement is safe;
    # a completed key also replays from this process's ledger
    supports_idempotency_key = True

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secret_runner: Callable[[str], str] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._transport = transport or urllib_transport
        self._secret_runner = secret_runner
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
        actor, doc, failure = load_bound_actor(self._store, input, context)
        if failure is not None or actor is None:
            return failure or InvocationResult.failed("actor_not_found", retryable=False)
        taken = self._model_taken(actor, doc)
        if taken is not None:
            return taken
        try:
            result = self._assess(input, actor, deadline)
        except _Refused as exc:
            return exc.result()
        if result.outcome == COMPLETED:
            self._ledger.put(idempotency_key, result)
        return result

    def replay(self, idempotency_key: str) -> InvocationResult | None:
        """The completed result already recorded for ``idempotency_key``, else None."""
        return self._ledger.get(idempotency_key)

    def refuse_before_admit(self, input: Mapping[str, Any]) -> InvocationResult | None:
        """The failure for work that must not start (``not_after`` passed or malformed)."""
        try:
            self._not_after(input)
        except _Refused as exc:
            return exc.result()
        return None

    def _model_taken(self, actor: Actor, doc: Mapping[str, Any] | None) -> InvocationResult | None:
        candidate = dict(doc or {})
        candidate.setdefault("id", actor.id)
        candidate.setdefault("kind", actor.kind)
        candidate.setdefault("model", actor.model)
        holder = senior_holder(self._store.find(ACTORS_COLLECTION, {"kind": "agent"}), candidate)
        if holder is None:
            return None
        return InvocationResult.failed(f"model_taken: {holder}", retryable=False)

    def _not_after(self, input: Mapping[str, Any]) -> datetime | None:
        when = _not_after(input.get("not_after"))
        if when is not None and self._clock() >= when:
            raise _Refused(DEADLINE_PASSED, "not_after has passed: the work may no longer start")
        return when

    def _remaining(self, input: Mapping[str, Any], deadline: datetime) -> float:
        not_after = self._not_after(input)
        if not_after is not None:
            deadline = min(deadline, not_after)
        timeout = (deadline - self._clock()).total_seconds()
        if timeout <= 0:
            raise _Refused(UNAVAILABLE, "attempt deadline already passed", retryable=True)
        return min(timeout, MAX_TIMEOUT)

    def _headers(self, params: Mapping[str, Any], domain: str) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Lobes-Model": f"decider:{domain}",
        }
        key = params.get("api_key")
        if key:
            try:
                headers["Authorization"] = "Bearer " + resolve(key, self._secret_runner)
            except SecretError as exc:
                raise _Refused("secret_unresolved", "params.api_key") from exc
        return headers

    def _assess(
        self, input: Mapping[str, Any], actor: Actor, deadline: datetime
    ) -> InvocationResult:
        if actor.kind != "agent":
            raise _Refused(
                "actor_kind_mismatch",
                f"decider.assess needs an agent actor, {actor.id!r} is {actor.kind!r}",
            )
        problem = check_decider_actor(actor)
        if problem:
            raise _Refused(ACTOR_INVALID, f"{actor.id!r}: {problem}")
        domain = _domain(actor.params) or DEFAULT_DOMAIN
        data = _build_request(input)
        headers = self._headers(actor.params, domain)  # may block on a grant lookup
        url = _systemone_url(actor.params["base_url"])
        timeout = self._remaining(input, deadline)
        try:
            status, reply_headers, raw = self._transport(url, data, headers, timeout)
        except (OSError, http.client.HTTPException) as exc:  # URLError/timeouts are OSErrors
            raise _Refused(UNAVAILABLE, type(exc).__name__, retryable=True) from exc
        if not 200 <= status < 300:
            raise _status_failure(status, raw)
        reply = _json_object(raw)
        questions = json.loads(data)["questions"]
        picks = _picks(reply.get("answers"), questions)
        return InvocationResult.completed(
            {
                "answers": reply["answers"],
                "picks": picks,
                "host": _host(reply_headers),
                "domain": domain,
                "usage": _usage(reply.get("usage")),
            }
        )
