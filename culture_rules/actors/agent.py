"""Agent actor adapters: one-shot ``colleague work`` and mesh tasks with correlation.

Two :class:`~culture_rules.engine.actorport.ActorPort` implementations:

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

Idempotency: both adapters remember results per idempotency key for the life of the
process, so a retried ``invoke`` never repeats the side effect (the mesh adapter never
re-sends). Across a process restart the target is not asked to dedupe, so a restart
mid-task can repeat work; the executor's claims (not this adapter) guard that window.

The real agentirc client (PyPI distribution ``agentirc-cli``, import name ``agentirc``;
the unrelated PyPI project named ``agentirc`` is NOT it) lives behind the optional
``agent`` extra and is imported lazily via :func:`load_agentirc_client`.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess  # nosec B404 - argv-list only, never a shell
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib import import_module
from typing import Any, Protocol, runtime_checkable

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = [
    "AgentActorError",
    "AgentClient",
    "ColleagueActor",
    "MeshAgentActor",
    "MeshReply",
    "load_agentirc_client",
    "parse_task_result",
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
        except (json.JSONDecodeError, ValueError):
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

    supports_idempotency_key = True

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
        if result.outcome != "blocked":
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

    supports_idempotency_key = True

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
        deadline: datetime,
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
        for reply in list(self._client.replies()):
            key = self._pending.pop(reply.correlation_id, None)
            if key is None:  # unrelated, or a duplicate of one already delivered
                continue
            if reply.error:
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
