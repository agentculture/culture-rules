"""Agent actor adapters: one-shot ``colleague work``, mesh tasks and async bridge sessions.

Three :class:`~culture_rules.engine.actorport.ActorPort` implementations:

:class:`ColleagueActor`
    Runs ``colleague work --repo <repo> --engine <engine> --model <model> --json -- <instruction>``
    as a subprocess (argv list, never a shell) and parses the ``TaskResult`` JSON it prints.
    ``status == "ok"`` completes the step; ``status == "error"`` (or unparseable output)
    fails it. The runner is injectable so tests never start real work.

:class:`MeshAgentActor`
    Sends a task to a ``<machine>-<agent>`` mesh nick through an :class:`AgentClient`
    and returns ``accepted``. The message carries a correlation id derived from the
    idempotency key; :meth:`MeshAgentActor.poll` matches incoming replies to pending
    tasks by that id and hands each completion to ``Executor.deliver``.

Idempotency: the colleague and mesh adapters remember *completed* results per idempotency
key for the life of the process, so an in-process re-ask of finished work replays it instead of
repeating the side effect, and the mesh adapter never re-sends a task still pending. A
failure is not remembered, so a retry after a definite failure runs the work again.
Neither ledger survives a restart, and neither target deduplicates by key (``colleague
work`` has no key; the mesh carries the correlation id but agents do not dedupe on it),
so both declare ``supports_idempotency_key = False``. The executor then never re-invokes
an attempt whose outcome is unknown (a resume after a crash, a lost acknowledgement, a
timeout): it fails the step with ``unsafe_retry`` unless the step is declared idempotent.
Claims alone do not guard that window: a claim only says which host may run the attempt.

The real agentirc client (PyPI distribution ``agentirc-cli``, import name ``agentirc``;
the unrelated PyPI project named ``agentirc`` is NOT it) lives behind the optional
``agent`` extra and is imported lazily via :func:`load_agentirc_client`.

:class:`BridgeAgentActor`
    Dispatches a step to a cultureagent bridge (``POST /v1/invocations``) over plain HTTP
    with the standard library (``urllib``; nothing from cultureagent is imported). The
    repo, head branch and head SHA come from the step's inputs (falling back to its
    config), never from the actor, so one bridge actor serves any repository and PR head.
    ``invoke`` records the invocation in the store (collection ``bridge_invocations``)
    *before* posting, then answers ``accepted``: the worker is free while the agent
    works for hours. The bridge reports back with callback events (``heartbeat``,
    ``progress``, ``completed``, ``failed``, ``blocked``) on the callback URL it was
    handed. :func:`record_bridge_event` is the store-only receiver for one event (any
    process with the store can host it: in production the API's
    ``POST /bridge-invocations/{id}/events``, or a stdlib :class:`BridgeCallbackServer`);
    it verifies the per-invocation callback token, ignores replays and
    stale sequences, and records a terminal result exactly once.
    :func:`redeliver_bridge` hands recorded results to ``Executor.deliver`` (through
    :func:`culture_rules.node.completions.deliver`, which also frees the actor's limit
    slot), so a completion recorded while no node was driving the run - or across a node
    restart - still finishes the step. A result belongs to one step attempt: once the step
    has moved on to a newer attempt, an older attempt's events are refused and its pending
    result is discarded (``superseded``), never delivered to the newer attempt. The bridge
    dedupes a transport retry by ``Idempotency-Key`` (one key per step attempt), but a new
    attempt is new work, so the adapter declares ``supports_idempotency_key = False`` like
    the other agents. Every wire field name lives in the ``-- bridge wire format --``
    section below.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import subprocess  # nosec B404 - argv-list only, never a shell
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from typing import Any, Protocol, runtime_checkable

from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.store.port import DuplicateKeyError

__all__ = [
    "AgentActorError",
    "AgentClient",
    "BRIDGE_CALLBACK_PATH",
    "BRIDGE_CALLBACK_RE",
    "BRIDGE_EVENT_STATUS",
    "BRIDGE_MAX_EVENT_BYTES",
    "BRIDGE_INVOCATIONS",
    "BridgeAgentActor",
    "BridgeCallbackServer",
    "ColleagueActor",
    "MeshAgentActor",
    "MeshReply",
    "load_agentirc_client",
    "build_invocation_request",
    "parse_task_result",
    "record_bridge_event",
    "redeliver_bridge",
    "result_from_terminal",
    "valid_mesh_nick",
]

_NICK_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.]*-[A-Za-z0-9][A-Za-z0-9_.-]*$")


class AgentActorError(RuntimeError):
    """Raised for malformed colleague output or a missing optional dependency."""


def valid_mesh_nick(nick: str) -> bool:
    """True for a ``<machine>-<agent>`` nick (no whitespace, a dash between the parts)."""
    return bool(_NICK_RE.match(nick or ""))


def _instruction(input: Mapping[str, Any]) -> str | None:
    for name in ("instruction", "prompt", "task", "text"):
        value = input.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


# -- one-shot colleague ---------------------------------------------------------------


def parse_task_result(stdout: str) -> dict[str, Any]:
    """Parse the ``TaskResult`` JSON ``colleague work --json`` prints.

    Tolerates log noise around the document by falling back to the last line that
    parses as a JSON object. Requires a ``status`` field.
    """
    candidates = [stdout.strip()]
    candidates += [ln.strip() for ln in reversed(stdout.splitlines()) if ln.strip().startswith("{")]
    for text in candidates:
        try:
            doc = json.loads(text)
        except ValueError:  # json.JSONDecodeError is a ValueError
            continue
        if isinstance(doc, dict):
            if "status" not in doc:
                raise AgentActorError("colleague output has no 'status' field")
            return doc
    raise AgentActorError("colleague output is not a JSON TaskResult")


Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def _default_runner(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 - fixed argv list, shell=False
        argv, capture_output=True, text=True, timeout=timeout, check=False, shell=False
    )


class ColleagueActor:
    """ActorPort running one-shot ``colleague work`` per step."""

    supports_idempotency_key = False  # in-memory ledger; colleague cannot dedupe by key

    def __init__(
        self,
        *,
        repo: str | None = None,
        engine: str | None = None,
        model: str | None = None,
        executable: str = "colleague",
        runner: Runner | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo, self.engine, self.model, self.executable = repo, engine, model, executable
        self._runner = runner or _default_runner
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._done: dict[str, InvocationResult] = {}

    def build_argv(
        self, instruction: str, repo: str, engine: str | None, model: str | None
    ) -> list[str]:
        argv = [self.executable, "work", "--repo", repo]
        if engine:
            argv += ["--engine", engine]
        if model:
            argv += ["--model", model]
        return argv + ["--json", "--", instruction]

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if idempotency_key in self._done:
            return self._done[idempotency_key]
        cfg = context.config or {}
        instruction = _instruction(input)
        repo = cfg.get("repo") or self.repo
        if not instruction:
            return InvocationResult.failed("no instruction in the step input", retryable=False)
        if not repo:
            return InvocationResult.failed("no repo configured for the agent", retryable=False)
        argv = self.build_argv(
            instruction, str(repo), cfg.get("engine") or self.engine, cfg.get("model") or self.model
        )
        timeout = max((deadline - self._now()).total_seconds(), 1.0)
        try:
            proc = self._runner(argv, timeout=timeout)
        except FileNotFoundError as exc:
            return InvocationResult.failed(f"colleague not found: {exc}", retryable=False)
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"colleague work timed out after {timeout:.0f}s") from exc
        result = self._to_result(proc)
        if result.outcome == COMPLETED:  # a failure is not cached: a retry runs again
            self._done[idempotency_key] = result
        return result

    @staticmethod
    def _to_result(proc: subprocess.CompletedProcess[str]) -> InvocationResult:
        try:
            doc = parse_task_result(proc.stdout or "")
        except AgentActorError as exc:
            detail = (proc.stderr or "").strip()[:500]
            return InvocationResult.failed(f"{exc} (exit {proc.returncode}): {detail}")
        if doc.get("status") == "ok":
            output = {
                k: doc.get(k)
                for k in ("task_id", "status", "summary", "changed_files", "branch", "pr_url")
            }
            output["changed_files"] = list(doc.get("changed_files") or [])
            return InvocationResult.completed(output)
        return InvocationResult.failed(str(doc.get("error") or doc.get("summary") or "task failed"))


# -- mesh -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MeshReply:
    """A reply seen on the mesh: ``correlation_id`` ties it to the task it answers."""

    correlation_id: str
    text: str
    sender: str = ""
    error: bool = False


@runtime_checkable
class AgentClient(Protocol):
    """The slice of an IRC/mesh client the adapter needs (sync; bridge async clients)."""

    def send(self, nick: str, text: str, correlation_id: str) -> None: ...

    def replies(self) -> Iterable[MeshReply]:
        """Return (and consume) replies received since the last call."""


class MeshAgentActor:
    """ActorPort sending a task to a mesh agent and completing on its correlated reply."""

    supports_idempotency_key = False  # in-memory ledger; mesh agents do not dedupe by key

    def __init__(self, *, client: AgentClient) -> None:
        self._client = client
        self._pending: dict[str, str] = {}  # correlation id -> idempotency key
        self._results: dict[str, InvocationResult] = {}  # key -> delivered result

    @staticmethod
    def correlation_id(idempotency_key: str) -> str:
        return "cr-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:16]

    def pending(self) -> list[str]:
        return list(self._pending.values())

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if idempotency_key in self._results:
            return self._results[idempotency_key]
        corr = self.correlation_id(idempotency_key)
        if corr in self._pending:
            return InvocationResult.accepted()
        nick = (context.config or {}).get("nick")
        if not isinstance(nick, str) or not valid_mesh_nick(nick):
            return InvocationResult.failed(
                f"mesh target must be a '<machine>-<agent>' nick, got {nick!r}", retryable=False
            )
        instruction = _instruction(input)
        if not instruction:
            return InvocationResult.failed("no instruction in the step input", retryable=False)
        self._client.send(nick, instruction, corr)  # raises => no ack, same key on retry
        self._pending[corr] = idempotency_key
        return InvocationResult.accepted()

    def poll(self, deliver: Callable[[str, InvocationResult], Any]) -> int:
        """Match fresh replies to pending tasks by correlation id and deliver each once."""
        delivered = 0
        for reply in self._client.replies():
            key = self._pending.pop(reply.correlation_id, None)
            if key is None:  # unrelated, or a duplicate of one already delivered
                continue
            if reply.error:  # not cached: a retry with the same key re-sends the task
                result = InvocationResult.failed(reply.text)
            else:
                result = InvocationResult.completed({"reply": reply.text, "sender": reply.sender})
                self._results[key] = result
            deliver(key, result)
            delivered += 1
        return delivered


def load_agentirc_client(importer: Callable[[str], Any] = import_module) -> Any:
    """Import and return ``agentirc.agent_client.AgentClient`` (needs the ``agent`` extra)."""
    try:
        return importer("agentirc.agent_client").AgentClient
    except ImportError as exc:
        raise AgentActorError(
            "agentirc is required for mesh agents; install with "
            "'pip install culture-rules[agent]' (distribution agentirc-cli)"
        ) from exc


# -- bridge wire format ---------------------------------------------------------------
# Every field name of the cultureagent bridge protocol (request ``protocol_version`` 1.0,
# result schema ``cultureagent.bridge.result/v1``) is spelled in this section and nowhere
# else, so following the bridge's schema is a change here only. Unknown keys in a result
# are kept (passed through as outputs), never an error.

PROTOCOL_VERSION = "1.0"
RESULT_SCHEMA = "cultureagent.bridge.result/v1"
INVOCATIONS_PATH = "/v1/invocations"
IDEMPOTENCY_HEADER = "Idempotency-Key"
ADDRESS_FIELDS = ("repo", "head_branch", "head_sha")
"""Where the bridge checks out: taken from the step's inputs (or config), never the actor."""
PASSTHROUGH_CONFIG = ("model", "sandbox", "mode")
"""Step-config keys forwarded into the bridge input (the actor supplies defaults); the qwen
bridge requires ``mode``."""
RESULT_FIELDS = (
    "schema",
    "invocation_id",
    "backend",
    "status",
    "summary",
    "repo",
    "head_branch",
    "head_before",
    "head_after",
    "commits",
    "changed_files",
    "diffstat",
    "dirty",
    "threads_addressed",
    "worktree",
    "error",
    "model",
    "session_id",
    "preserve",
)
"""The result keys; each becomes a step output of the same name (None when absent)."""
TURN_ENDED = frozenset({"completed", "no_changes", "uncommitted", "permission_blocked"})
"""Result statuses where the agent's turn ran to its end: the step completes."""
TERMINAL_KINDS = ("completed", "failed", "blocked")
NON_TERMINAL_KINDS = ("accepted", "heartbeat", "progress", "artifact", "signal")
NON_RETRYABLE_CLASSES = frozenset({"actor_rejected_input", "credential", "provision"})
"""Error classes where retrying cannot help (bad input, missing credential, no checkout)."""
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")


def build_invocation_request(
    *, input: Mapping[str, Any], callback_url: str, callback_token: str
) -> dict[str, Any]:
    """The ``POST /v1/invocations`` body for one step attempt (always asynchronous)."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "input": {**input, "async": True},
        "callback": {"url": callback_url, "token": callback_token},
    }


def result_from_terminal(kind: str, payload: Mapping[str, Any] | None) -> InvocationResult:
    """Map a terminal event, or a synchronous ``200`` body (kind ``completed``), to a result.

    The bridge result sits in ``payload["result"]``. A ``completed`` event whose status is
    one of :data:`TURN_ENDED` (or absent) completes the step with every result key as an
    output. Anything else fails it: the class comes from the event (``class``/``message``)
    or the result's ``error``, and is retryable unless it is in
    :data:`NON_RETRYABLE_CLASSES`. ``blocked`` (the agent cannot proceed) never retries.
    """
    payload = payload if isinstance(payload, Mapping) else {}
    raw = payload.get("result")
    result: dict[str, Any] = dict(raw) if isinstance(raw, Mapping) else {}
    for name in RESULT_FIELDS:
        result.setdefault(name, None)
    status = result.get("status")
    if kind == "completed" and (status is None or status in TURN_ENDED):
        return InvocationResult.completed(result)
    if kind == "blocked":
        reason = payload.get("message") or status or "the agent cannot proceed"
        return InvocationResult.failed(f"blocked: {reason}", retryable=False)
    error = result.get("error") if isinstance(result.get("error"), Mapping) else {}
    cls = str(payload.get("class") or error.get("class") or "execution")
    message = str(payload.get("message") or error.get("message") or status or "bridge failure")
    return InvocationResult.failed(f"{cls}: {message}", retryable=cls not in NON_RETRYABLE_CLASSES)


# -- bridge transport -----------------------------------------------------------------

Transport = Callable[[str, str, "bytes | None", Mapping[str, str], float], tuple[int, bytes]]
"""``(method, url, body, headers, timeout) -> (status, body)``; injectable for tests."""


class BridgeUnreachable(OSError):
    """The bridge could not be reached; ``definite`` means the request was never received."""

    def __init__(self, message: str, *, definite: bool) -> None:
        super().__init__(message)
        self.definite = definite


def _urllib_transport(
    method: str, url: str, body: bytes | None, headers: Mapping[str, str], timeout: float
) -> tuple[int, bytes]:
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise BridgeUnreachable(f"refusing a non-http(s) bridge URL {url!r}", definite=True)
    req = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 - scheme checked
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        # A refused connection or an unresolvable host never reached the bridge.
        definite = isinstance(exc.reason, (ConnectionRefusedError, LookupError)) or (
            type(exc.reason).__name__ == "gaierror"
        )
        raise BridgeUnreachable(f"bridge unreachable: {exc.reason}", definite=definite) from exc


# -- bridge invocations in the store --------------------------------------------------

BRIDGE_INVOCATIONS = "bridge_invocations"
BRIDGE_CALLBACK_PATH = "/bridge-invocations/{id}/events"
"""The callback route (the API's ``POST /bridge-invocations/{id}/events``), appended to an
actor's ``callback_url`` unless that URL already carries an ``{id}`` placeholder."""
BRIDGE_CALLBACK_RE = re.compile(r"^/bridge-invocations/(bri_[0-9a-f]{24})/events$")
"""Exactly the callback paths :func:`bridge_invocation_id` can produce."""
BRIDGE_SCHEMA_VERSION = 1
_DISPATCHING, _ACCEPTED, _COMPLETED, _FAILED = "dispatching", "accepted", "completed", "failed"
_REJECTED, _EXPIRED, _SUPERSEDED = "rejected", "expired", "superseded"
_OPEN = (_DISPATCHING, _ACCEPTED)

RECORDED, DUPLICATE, UNKNOWN, UNAUTHORIZED, EXPIRED, INVALID = (
    "recorded",
    "duplicate",
    "unknown",
    "unauthorized",
    "expired",
    "invalid",
)
"""What :func:`record_bridge_event` did with one callback event."""


def bridge_invocation_id(idempotency_key: str, attempt: int) -> str:
    """Deterministic store id of one step attempt's bridge invocation (URL-safe)."""
    digest = hashlib.sha256(f"{idempotency_key}:{attempt}".encode()).hexdigest()[:24]
    return f"bri_{digest}"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _token_ok(doc: Mapping[str, Any], token: Any) -> bool:
    if not isinstance(token, str) or not token:
        return False
    presented = _token_hash(token)
    return any(hmac.compare_digest(presented, h) for h in doc.get("token_hashes") or ())


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _superseded(store: Any, doc: Mapping[str, Any]) -> bool:
    """Whether the step has moved on to a newer attempt than this invocation's."""
    from culture_rules.node.completions import step_attempt  # the node layer; lazily

    current = step_attempt(store, doc["idempotency_key"])
    return isinstance(current, int) and current > doc["attempt"]


def _supersede(store: Any, doc: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    return store.update_if(
        BRIDGE_INVOCATIONS, doc["id"], expected, {"status": _SUPERSEDED, "pending_delivery": False}
    ).won


def record_bridge_event(
    store: Any,
    invocation_id: str,
    token: Any,
    event: Any,
    *,
    clock: Callable[[], datetime] | None = None,
) -> str:
    """Record one callback event for a stored bridge invocation; return what happened.

    Store-only, so any process with the store can receive callbacks. The bearer
    ``token`` must be the one handed to the bridge for this invocation (only its hash
    is stored). Non-terminal events update liveness (``last_heartbeat_at``) and are
    ignored when their ``sequence`` is not newer; the first terminal event records the
    result for :func:`redeliver_bridge`, later ones are :data:`DUPLICATE`. Events for an
    attempt the step no longer runs (expired, or a newer attempt started) are
    :data:`EXPIRED`; a terminal one marks the invocation ``superseded``.
    """
    now = _iso((clock or _utcnow)())
    for _ in range(5):  # compare-and-set; a lost race re-reads
        doc = (
            store.get(BRIDGE_INVOCATIONS, invocation_id) if isinstance(invocation_id, str) else None
        )
        if doc is None:
            return UNKNOWN
        if not _token_ok(doc, token):
            return UNAUTHORIZED
        if not isinstance(event, Mapping):
            return INVALID
        kind, seq = event.get("kind"), event.get("sequence")
        if kind not in TERMINAL_KINDS + NON_TERMINAL_KINDS:
            return INVALID
        if not isinstance(seq, int) or isinstance(seq, bool):
            return INVALID
        if doc["status"] in (_EXPIRED, _SUPERSEDED):
            return EXPIRED
        payload = event.get("payload")
        payload = payload if isinstance(payload, Mapping) else {}
        if kind in TERMINAL_KINDS:
            if doc["status"] not in _OPEN:
                return DUPLICATE
            if _superseded(store, doc):  # a newer attempt runs: never finish it with this
                _supersede(store, doc, {"status": doc["status"]})
                return EXPIRED
            result = result_from_terminal(str(kind), payload)
            changes = {
                "status": _COMPLETED if result.outcome == COMPLETED else _FAILED,
                "result": result.to_dict(),
                "pending_delivery": True,
                "finished_at": now,
                "last_event_at": now,
                "last_sequence": max(seq, doc.get("last_sequence") or 0),
            }
            expected = {"status": doc["status"]}
        else:
            if seq <= (doc.get("last_sequence") or 0):
                return DUPLICATE
            changes = {"last_sequence": seq, "last_event_at": now}
            if kind == "heartbeat":
                changes["last_heartbeat_at"] = now
            if kind == "accepted" and payload.get("invocation_id") and not doc.get("invocation_id"):
                changes["invocation_id"] = str(payload["invocation_id"])
            expected = {"status": doc["status"], "last_sequence": doc.get("last_sequence") or 0}
        if store.update_if(BRIDGE_INVOCATIONS, invocation_id, expected, changes).won:
            return RECORDED
    return DUPLICATE  # pragma: no cover - sustained contention: the bridge retries


def _deliver_one(store: Any, executor: Any, doc: Mapping[str, Any]) -> bool:
    """Deliver a recorded result (freeing the actor's slot) and flag it; True iff it
    changed the run. A result for an attempt the step has moved past is discarded
    (``superseded``), never delivered: the delivery names its attempt, and
    ``Executor.deliver`` refuses it inside its compare-and-set when the step is on
    another attempt (the early check only spares the call)."""
    from culture_rules.node import completions  # the node layer; imported lazily

    if _superseded(store, doc):
        _supersede(store, doc, {"pending_delivery": True})
        return False
    result = InvocationResult.from_dict(doc["result"])
    key = doc["idempotency_key"]
    changed = completions.deliver(store, executor, key, result, attempt=doc["attempt"])
    if not changed and _superseded(store, doc):  # a newer attempt started meanwhile
        _supersede(store, doc, {"pending_delivery": True})
        return False
    store.update_if(
        BRIDGE_INVOCATIONS,
        doc["id"],
        {"pending_delivery": True},
        {"pending_delivery": False, "delivered_at": _iso(_utcnow())},
    )
    return changed


def redeliver_bridge(store: Any, executor: Any, invocation_id: str | None = None) -> int:
    """Hand recorded bridge results to ``Executor.deliver``; return how many changed a run.

    Safe to repeat and to run on several hosts: the run changes once (compare-and-set,
    finished steps ignore deliveries). A node calls this every cycle so a result recorded
    by another process, or before a restart, still finishes its step.
    """
    if invocation_id is not None:
        doc = store.get(BRIDGE_INVOCATIONS, invocation_id)
        pending = [doc] if doc is not None and doc.get("pending_delivery") else []
    else:
        pending = store.find(BRIDGE_INVOCATIONS, {"pending_delivery": True})
    return sum(1 for doc in pending if _deliver_one(store, executor, doc))


# -- the adapter ----------------------------------------------------------------------


class BridgeAgentActor:
    """ActorPort dispatching a step to a cultureagent bridge; completes via callbacks."""

    supports_idempotency_key = False  # a new attempt is new work at the bridge

    def __init__(
        self,
        store: Any,
        *,
        bridge_url: str,
        callback_url: str | None,
        token: str | None = None,
        resolve_secret: Callable[[str], str] | None = None,
        defaults: Mapping[str, Any] | None = None,
        actor_id: str | None = None,
        transport: Transport | None = None,
        clock: Callable[[], datetime] | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        self._store = store
        self.bridge_url = bridge_url.rstrip("/")
        self.callback_url = (callback_url or "").rstrip("/")
        self._token_ref = token
        self._resolve = resolve_secret
        self._defaults = {k: v for k, v in (defaults or {}).items() if v is not None}
        self.actor_id = actor_id
        self._transport = transport or _urllib_transport
        self._clock = clock or _utcnow
        self._request_timeout = request_timeout
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):
            ensure(BRIDGE_INVOCATIONS)

    def callback_for(self, invocation_id: str) -> str:
        """The callback URL handed to the bridge for one invocation."""
        if "{id}" in self.callback_url:
            return self.callback_url.replace("{id}", invocation_id)
        return self.callback_url + BRIDGE_CALLBACK_PATH.format(id=invocation_id)

    # ---------------------------------------------------------------- input

    def bridge_input(
        self, input: Mapping[str, Any], config: Mapping[str, Any]
    ) -> tuple[dict[str, Any] | None, str | None]:
        """The bridge's ``input`` object, or ``(None, problem)``."""
        instruction = _instruction(input) or _instruction(config)
        if not instruction:
            return None, "no instruction in the step input"
        out: dict[str, Any] = {k: v for k, v in input.items() if v is not None}
        for name in ADDRESS_FIELDS:
            value = input.get(name) or config.get(name)
            if not isinstance(value, str) or not value.strip():
                return None, f"the step input has no {name!r} (repo, head_branch and head_sha)"
            out[name] = value.strip()
        if not _SHA_RE.match(out["head_sha"]):
            return None, f"head_sha {out['head_sha']!r} is not a commit SHA"
        for name in PASSTHROUGH_CONFIG:
            value = config.get(name, self._defaults.get(name))
            if value is not None:
                out.setdefault(name, value)
        out["instruction"] = instruction
        return out, None

    # ---------------------------------------------------------------- invoke

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        payload, problem = self.bridge_input(input, context.config or {})
        if problem:
            return InvocationResult.failed(problem, retryable=False)
        if not self.callback_url:
            return InvocationResult.failed("the bridge actor has no callback_url", retryable=False)
        try:
            auth = self._bearer()
        except Exception as exc:  # noqa: BLE001 - a secret error is a config failure
            return InvocationResult.failed(
                f"cannot resolve the bridge token: {exc}", retryable=False
            )
        doc_id = bridge_invocation_id(idempotency_key, context.attempt)
        self._expire_previous(idempotency_key, context.attempt)
        callback_token = secrets.token_urlsafe(32)
        doc = self._claim(doc_id, idempotency_key, context, callback_token)
        if doc["status"] == _ACCEPTED:
            return InvocationResult.accepted()
        if doc["status"] in (_COMPLETED, _FAILED):  # the callback beat this re-invoke
            return InvocationResult.from_dict(doc["result"])
        attempt_id = f"{idempotency_key}#{context.attempt}"
        body = build_invocation_request(
            input=payload or {},
            callback_url=self.callback_for(doc_id),
            callback_token=callback_token,
        )
        headers = {"Content-Type": "application/json", IDEMPOTENCY_HEADER: attempt_id}
        if auth:
            headers["Authorization"] = f"Bearer {auth}"
        data = json.dumps(body, default=str).encode("utf-8")
        # Checked right before the POST: the claim and the secret lookup may have used up the
        # attempt, and work dispatched after its deadline could overlap a replacement attempt.
        remaining = (deadline - self._clock()).total_seconds()
        if remaining <= 0:
            problem = "the attempt deadline passed before the bridge was called"
            self._settle(doc_id, _REJECTED, error=problem)
            return InvocationResult.failed(problem)
        timeout = min(self._request_timeout, remaining)
        try:
            status, raw = self._transport(
                "POST", self.bridge_url + INVOCATIONS_PATH, data, headers, timeout
            )
        except BridgeUnreachable as exc:
            if not exc.definite:
                raise  # the request may have arrived: the outcome is unknown
            self._settle(doc_id, _REJECTED, error=str(exc))
            return InvocationResult.failed(str(exc))
        return self._response(doc_id, status, raw)

    def _bearer(self) -> str | None:
        if not self._token_ref:
            return None
        if self._resolve is not None:
            return self._resolve(self._token_ref)
        from culture_rules.actors import secrets as secret_refs

        return secret_refs.resolve_or_literal(self._token_ref)

    def _claim(
        self, doc_id: str, key: str, context: InvocationContext, callback_token: str
    ) -> Mapping[str, Any]:
        """Insert this attempt's invocation (before posting, so an early callback finds it),
        or add a fresh callback token to one left ``dispatching``/``rejected``."""
        now = _iso(self._clock())
        doc = {
            "id": doc_id,
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "idempotency_key": key,
            "run_id": context.run_id,
            "step_id": context.step_id,
            "attempt": context.attempt,
            "actor": self.actor_id or context.actor,
            "bridge_url": self.bridge_url,
            "status": _DISPATCHING,
            "token_hashes": [_token_hash(callback_token)],
            "invocation_id": None,
            "last_sequence": 0,
            "last_heartbeat_at": None,
            "pending_delivery": False,
            "created_at": now,
        }
        try:
            return self._store.insert(BRIDGE_INVOCATIONS, doc)
        except DuplicateKeyError:
            existing = self._store.get(BRIDGE_INVOCATIONS, doc_id)
        if existing["status"] not in (_DISPATCHING, _REJECTED):
            return existing
        hashes = [*existing.get("token_hashes", ()), _token_hash(callback_token)]
        res = self._store.update_if(
            BRIDGE_INVOCATIONS,
            doc_id,
            {"status": existing["status"]},
            {"status": _DISPATCHING, "token_hashes": hashes},
        )
        return res.document if res.won else self._store.get(BRIDGE_INVOCATIONS, doc_id)

    def _expire_previous(self, key: str, attempt: int) -> None:
        """A new attempt supersedes older ones: their callbacks are refused from now on,
        and a result of theirs still waiting for delivery is discarded."""
        for old in self._store.find(BRIDGE_INVOCATIONS, {"idempotency_key": key}):
            if old["attempt"] >= attempt:
                continue
            if old["status"] in _OPEN:
                self._store.update_if(
                    BRIDGE_INVOCATIONS, old["id"], {"status": old["status"]}, {"status": _EXPIRED}
                )
            elif old.get("pending_delivery"):
                _supersede(self._store, old, {"status": old["status"], "pending_delivery": True})

    def _settle(self, doc_id: str, status: str, **changes: Any) -> bool:
        return self._store.update_if(
            BRIDGE_INVOCATIONS, doc_id, {"status": _DISPATCHING}, {"status": status, **changes}
        ).won

    def _response(self, doc_id: str, status: int, raw: bytes) -> InvocationResult:
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            body = {}
        body = body if isinstance(body, dict) else {}
        if status == 202:
            self._settle(
                doc_id,
                _ACCEPTED,
                invocation_id=body.get("invocation_id"),
                heartbeat_after_seconds=body.get("heartbeat_after_seconds"),
                accepted_at=_iso(self._clock()),
            )  # a lost race means a terminal callback already landed: it is delivered later
            return InvocationResult.accepted()
        if status == 200:
            result = result_from_terminal("completed", body)
            self._settle(doc_id, _COMPLETED, result=result.to_dict())
            return result
        error = f"bridge answered {status}: {body.get('error') or 'no detail'}"
        self._settle(doc_id, _REJECTED, error=error)
        if status in (429, 503):  # at capacity or briefly unavailable: ask again later
            return InvocationResult.blocked(error)
        return InvocationResult.failed(error, retryable=status >= 500 or status in (408, 409))


# -- the callback endpoint ------------------------------------------------------------

BRIDGE_MAX_EVENT_BYTES = 1 << 20
BRIDGE_EVENT_STATUS = {  # what a receiver answers for each record_bridge_event outcome
    RECORDED: 200,
    DUPLICATE: 200,
    INVALID: 400,
    UNAUTHORIZED: 401,
    UNKNOWN: 404,
    EXPIRED: 410,
}


class BridgeCallbackServer:
    """A stdlib HTTP endpoint for bridge callbacks (``POST`` :data:`BRIDGE_CALLBACK_PATH`).

    Each event goes through :func:`record_bridge_event`; a recorded terminal event is
    delivered at once when an ``executor`` is given (else by the next
    :func:`redeliver_bridge`). Bind it where the bridges can reach it and set the bridge
    actors' ``callback_url`` to :attr:`url` (or the address in front of it).
    """

    def __init__(
        self,
        store: Any,
        *,
        executor: Any = None,
        host: str = "127.0.0.1",
        port: int = 0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store, self._executor, self._clock = store, executor, clock
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args: Any) -> None:  # quiet; the node logs
                return

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                code, body = outer.handle(
                    self.path,
                    self.headers.get("Authorization", ""),
                    self._read(),
                )
                data = json.dumps(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _read(self) -> bytes | None:
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    return None
                if length < 0 or length > BRIDGE_MAX_EVENT_BYTES:
                    return None
                return self.rfile.read(length)

        self._server = ThreadingHTTPServer((host, port), Handler)
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def handle(self, path: str, authorization: str, raw: bytes | None) -> tuple[int, dict]:
        """One callback request -> ``(http status, body)`` (also usable without a socket)."""
        match = BRIDGE_CALLBACK_RE.fullmatch(path.split("?", 1)[0])
        if match is None:
            return 404, {"error": "not found"}
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        try:
            event = json.loads(raw.decode("utf-8")) if raw is not None else None
        except (ValueError, UnicodeDecodeError):
            event = None
        outcome = record_bridge_event(self._store, match.group(1), token, event, clock=self._clock)
        if outcome == RECORDED and self._executor is not None:
            try:
                redeliver_bridge(self._store, self._executor, match.group(1))
            except Exception:  # noqa: BLE001 - recorded; the node's redeliver retries
                pass  # nosec B110
        return BRIDGE_EVENT_STATUS[outcome], {"status": outcome}

    def start(self) -> str:
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            name="bridge-callbacks",
            daemon=True,
        )
        self._thread.start()
        return self.url

    def close(self) -> None:
        if self._thread is not None:  # shutdown() blocks unless serve_forever is running
            self._server.shutdown()
            self._thread.join(timeout=2.0)
            self._thread = None
        self._server.server_close()
