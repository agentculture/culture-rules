"""Built-ins ``handoff.export`` and ``handoff.import``: the Codex fixer's commits, carried
from spark to spark2's gate through the store (#39, c10, h9).

The Codex fixer runs on spark, under its own unprivileged account; the test gate runs on
spark2 and reads a worktree on its own machine. Nothing here uses ssh or a network share:
the commits travel as one size-capped git pack in a store document.

``handoff.export`` (placed on spark)
====================================

Inputs: ``worktree`` (the Codex bridge result's absolute path on spark), ``start_sha``
(``head_before``) and ``head_after``, each a full lowercase hex SHA. Every git command runs
through a run-as seam configured by ``CULTURE_RULES_HANDOFF_RUN_AS`` (for example
``sudo -n -u culture-codex-fixer --``; validated like the gate's
``CULTURE_RULES_GATE_RUN_AS``: an argv prefix, never ssh, su or a shell; **unset, it
refuses** ``handoff_runner_unconfigured``), with the fixer's own git config, templates,
hooks and fsmonitor ignored. It checks that the worktree's ``HEAD`` is ``head_after``
(``handoff_head_mismatch``) and that ``head_after`` descends from ``start_sha``
(``handoff_not_descendant``), then packs ``start_sha..head_after`` (``git pack-objects
--revs --thin``: objects ``start_sha`` already has may be left out as delta bases). A pack
over ``config.max_bytes`` (default :data:`DEFAULT_MAX_BYTES`, at most
:data:`MAX_MAX_BYTES`, so the base64 document stays well under MongoDB's 16 MB) fails the
step ``handoff_too_large`` and is never stored. Otherwise it is stored in
:data:`HANDOFFS_COLLECTION` as ``{id, pack (base64), size, sha256, start_sha, head_after,
run_id, step_id, created_at}`` and the step outputs ``handoff_id``, ``size`` and
``sha256``. The id is derived from the run, the step and ``head_after``, so a re-run
replaces its own document. Documents older than :data:`HANDOFF_TTL_S` are pruned on each
export.

``handoff.import`` (placed on spark2)
=====================================

Inputs: ``handoff_id``, ``start_sha``, ``head_after``, ``clone_url`` (a GitHub HTTPS clone
URL) and optionally ``base_sha``. Every command runs as the fixer user through the gate's
own seam (``CULTURE_RULES_GATE_RUN_AS``, so the gate can read what it builds). Before
anything runs, the stored document must name the same ``start_sha`` and ``head_after``
(``handoff_mismatch``) and its pack must have the stored size and sha256
(``handoff_corrupt``). Then, as the fixer user: a fresh ``mktemp -d`` workspace
(``culture_rules_handoff.*`` in the account's temp dir; ones older than
:data:`WORKTREE_TTL_MIN` minutes are removed first), ``git init``, ``origin`` set to
``clone_url``, a fetch of ``start_sha`` (and ``base_sha``) by SHA
(``handoff_fetch_failed``, retryable), ``git index-pack --stdin --strict --fix-thin`` of
the pack on a descriptor the node opened (``handoff_corrupt``), a check that
``head_after`` is now a commit (``handoff_corrupt``) descending from ``start_sha``
(``handoff_not_descendant``) and a detached checkout whose ``HEAD`` is exactly
``head_after`` (``handoff_checkout_failed``). Any refusal removes the workspace: there is
no ``worktree`` output, so the gate never judges a half-built tree. On success the
handoff document is deleted and the step outputs ``worktree``, ``start_sha`` and
``commit_sha`` (the gate's inputs) plus ``head_before`` and ``head_after`` (the agent
step's own names). The gate then judges exactly the commit the Codex fixer made: git
object ids are content hashes, and ``index-pack --strict`` verifies every one.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import shlex
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any

from culture_rules.actors.gate import (
    _CLONE_URL_RE,
    NO_NEW_PRIVS_FIX,
    RUN_AS_ENV,
    TIMED_OUT,
    UNAVAILABLE,
    CommandRunner,
    RunAs,
    _hard_git,
    _Refusal,
    _sha_input,
    _stderr_tail,
    no_new_privs,
    run_as_from_env,
)
from culture_rules.engine.actorport import InvocationContext, InvocationResult

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_BYTES",
    "EXPORT_BUILTIN",
    "HANDOFFS_COLLECTION",
    "HANDOFF_RUN_AS_ENV",
    "HANDOFF_TTL_S",
    "IMPORT_BUILTIN",
    "MAX_MAX_BYTES",
    "WORKTREE_TTL_MIN",
    "HandoffExportPort",
    "HandoffImportPort",
    "handoff_id",
    "handoff_run_as_from_env",
]

EXPORT_BUILTIN = "handoff.export"
IMPORT_BUILTIN = "handoff.import"
HANDOFFS_COLLECTION = "handoffs"
HANDOFF_RUN_AS_ENV = "CULTURE_RULES_HANDOFF_RUN_AS"
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
MAX_MAX_BYTES = 10 * 1024 * 1024  # base64 of 10 MiB is ~13.4 MiB: under the 16 MB document
HANDOFF_TTL_S = 24 * 3600.0
WORKTREE_TTL_MIN = 2 * 24 * 60
_MARGIN_S = 5.0
_CLEANUP_S = 60.0
_ID_RE = re.compile(r"^hof_[0-9a-f]{32}$")
_WORKSPACE_PREFIX = "culture_rules_handoff."
_REPO_DIR = "repo"
_HEAD_COMMIT = "HEAD^{commit}"
_HANDOFF_CORRUPT = "handoff_corrupt"
_NOT_DESCENDANT = "handoff_not_descendant"
_CHECKOUT_FAILED = "handoff_checkout_failed"
_SOURCE_UNAVAILABLE = "handoff_source_unavailable"


def handoff_id(run_id: str, step_id: str, head_after: str) -> str:
    """The deterministic document id of one export (a re-run replaces its own)."""
    digest = hashlib.sha256(f"{run_id}\0{step_id}\0{head_after}".encode()).hexdigest()
    return f"hof_{digest[:32]}"


def handoff_run_as_from_env(
    environ: Mapping[str, str] | None = None,
) -> tuple[RunAs | None, str]:
    """The export's :class:`~culture_rules.actors.gate.RunAs` (or ``None``) and, when
    ``None``, why. Same rules as the gate's: an argv prefix, never ssh, su or a shell."""
    environ = os.environ if environ is None else environ
    value = (environ.get(HANDOFF_RUN_AS_ENV) or "").strip()
    if not value:
        return None, (
            f"{HANDOFF_RUN_AS_ENV} is not set; the export never runs git as the node user "
            "(set it to a prefix such as 'sudo -n -u culture-codex-fixer --')"
        )
    try:
        return RunAs(shlex.split(value)), ""
    except ValueError as exc:
        return None, f"{HANDOFF_RUN_AS_ENV}: {exc}"


def _blocked(run_as: RunAs | None, env_name: str, check: Callable[[], bool | None]) -> str:
    """Why a sudo prefix cannot work in this process (``no_new_privs``), else ``""``."""
    if run_as is None or not run_as.uses_sudo or not check():
        return ""
    reason = (
        f"{env_name} starts with sudo but the node runs with no_new_privs set "
        f"(systemd NoNewPrivileges=true), so sudo refuses; {NO_NEW_PRIVS_FIX}"
    )
    log.error("handoff: %s", reason)
    return reason


def _abs_path(input: Mapping[str, Any], name: str) -> str:
    value = input.get(name)
    if not isinstance(value, str) or not os.path.isabs(value) or "\x00" in value:
        raise _Refusal("bad_input", f"{name} must be an absolute path")
    return value


def _max_bytes(config: Mapping[str, Any]) -> int:
    cap = config.get("max_bytes", DEFAULT_MAX_BYTES)
    if not isinstance(cap, int) or isinstance(cap, bool) or not 0 < cap <= MAX_MAX_BYTES:
        raise _Refusal("bad_config", f"max_bytes must be 1..{MAX_MAX_BYTES}")
    return cap


def _expired(created_at: Any, cutoff: datetime) -> bool:
    """Whether a document stamped ``created_at`` is older than ``cutoff`` (unreadable: yes)."""
    try:
        return datetime.fromisoformat(str(created_at)) < cutoff
    except (TypeError, ValueError):
        return True


class _Call:
    """One invocation's run-as commands, each bounded by the step's deadline."""

    def __init__(
        self,
        run_as: CommandRunner,
        tmp: str,
        deadline: datetime,
        clock: Callable[[], datetime],
    ) -> None:
        self._run_as, self.tmp = run_as, tmp
        self._deadline, self._clock = deadline, clock

    def left(self) -> float:
        left = (self._deadline - self._clock()).total_seconds() - _MARGIN_S
        if left <= 0:
            raise _Refusal("deadline_exceeded", retryable=True)
        return left

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        stdin: IO[bytes] | None = None,
        out: IO[bytes] | None = None,
    ) -> tuple[int, bytes, str]:
        """``(exit code, stdout, stderr tail)``; stdout goes to ``out`` when given."""
        with (
            tempfile.TemporaryFile(dir=self.tmp) as own,
            tempfile.TemporaryFile(dir=self.tmp) as err,
        ):
            rc = self._run_as(
                list(argv),
                cwd=cwd,
                timeout=self.left(),
                stdout=own if out is None else out,
                stdin=stdin,
                merge_stderr=False,
                stderr=err,
            )
            if rc == UNAVAILABLE:
                raise _Refusal("handoff_runner_unavailable", "the run-as command could not start")
            if rc == TIMED_OUT:
                self.left()
                raise _Refusal("handoff_timeout", "a run-as command timed out", retryable=True)
            own.seek(0)
            return rc, own.read(), _stderr_tail(err) if rc else ""

    def ok(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        code: str,
        what: str,
        stdin: IO[bytes] | None = None,
        retryable: bool = False,
    ) -> str:
        """Run ``argv``; a non-zero exit is the refusal ``code``. Returns stdout as text."""
        rc, out, tail = self.run(argv, cwd=cwd, stdin=stdin)
        if rc != 0:
            detail = f"{what} failed (exit {rc})" + (f": {tail}" if tail else "")
            raise _Refusal(code, detail, retryable=retryable)
        return out.decode("utf-8", errors="replace")

    def remove(self, path: str) -> None:
        """``rm -rf`` a run-as-owned directory, with its own timeout (even past the deadline)."""
        with tempfile.TemporaryFile(dir=self.tmp) as out:
            self._run_as(["rm", "-rf", "--", path], cwd="/", timeout=_CLEANUP_S, stdout=out)


def _descends(call: _Call, cwd: str, start: str, head: str, missing_code: str) -> None:
    """``head`` descends from ``start`` (``handoff_not_descendant``), else ``missing_code``
    when git cannot tell (a commit is not there)."""
    rc, _, tail = call.run(_hard_git("merge-base", "--is-ancestor", start, head), cwd=cwd)
    if rc == 1:
        raise _Refusal(
            _NOT_DESCENDANT, f"head_after {head[:12]} does not descend from {start[:12]}"
        )
    if rc != 0:
        raise _Refusal(missing_code, f"git merge-base failed (exit {rc}): {tail}")


class _Port:
    """What both built-ins share: the run-as seam, its refusals and the result shape."""

    supports_idempotency_key = True

    def __init__(
        self,
        store: Any,
        *,
        run_as: CommandRunner | None,
        unconfigured_reason: str = "",
        blocked_reason: str = "",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._run_as = run_as
        self._why = unconfigured_reason or "the run-as prefix is not set"
        self._blocked = blocked_reason
        self._clock = clock or (lambda: datetime.now(UTC))
        ensure = getattr(store, "ensure_collections", None)
        if callable(ensure):
            ensure(HANDOFFS_COLLECTION)

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        try:
            return InvocationResult.completed(self._step(input, deadline, context))
        except _Refusal as exc:
            return InvocationResult.failed(exc.message(), retryable=exc.retryable)

    def _step(
        self, input: Mapping[str, Any], deadline: datetime, context: InvocationContext
    ) -> dict[str, Any]:
        raise NotImplementedError  # pragma: no cover

    def _runner(self) -> CommandRunner:
        if self._run_as is None:
            raise _Refusal("handoff_runner_unconfigured", self._why)
        if self._blocked:
            raise _Refusal("run_as_blocked", self._blocked)
        return self._run_as


class HandoffExportPort(_Port):
    """ActorPort for the built-in ``handoff.export`` code step (module doc)."""

    @classmethod
    def from_env(
        cls,
        store: Any,
        environ: Mapping[str, str] | None = None,
        *,
        no_new_privs: Callable[[], bool | None] = no_new_privs,
    ) -> HandoffExportPort:
        run_as, why = handoff_run_as_from_env(environ)
        blocked = _blocked(run_as, HANDOFF_RUN_AS_ENV, no_new_privs)
        return cls(store, run_as=run_as, unconfigured_reason=why, blocked_reason=blocked)

    def _step(
        self, input: Mapping[str, Any], deadline: datetime, context: InvocationContext
    ) -> dict[str, Any]:
        worktree = _abs_path(input, "worktree")
        start, head = _sha_input(input, "start_sha"), _sha_input(input, "head_after")
        cap = _max_bytes(context.config or {})
        runner = self._runner()
        tmp = tempfile.mkdtemp(prefix="culture-rules-handoff-")
        try:
            call = _Call(runner, tmp, deadline, self._clock)
            self._check_head(call, worktree, start, head)
            pack = self._pack(call, worktree, start, head, cap)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return self._store_pack(pack, start, head, context)

    @staticmethod
    def _check_head(call: _Call, worktree: str, start: str, head: str) -> None:
        actual = call.ok(
            _hard_git("rev-parse", "--verify", _HEAD_COMMIT),
            cwd=worktree,
            code=_SOURCE_UNAVAILABLE,
            what="git rev-parse HEAD in the worktree",
        ).strip()
        if actual != head:
            raise _Refusal(
                "handoff_head_mismatch",
                f"the worktree's HEAD is {actual[:12]}, not head_after {head[:12]}",
            )
        _descends(call, worktree, start, head, _SOURCE_UNAVAILABLE)

    @staticmethod
    def _pack(call: _Call, worktree: str, start: str, head: str, cap: int) -> bytes:
        """``start..head`` as one thin pack, read back only when it fits ``cap``."""
        revs = os.path.join(call.tmp, "revs")
        Path(revs).write_text(f"{head}\n^{start}\n")
        path = os.path.join(call.tmp, "out.pack")
        argv = _hard_git("pack-objects", "--revs", "--thin", "--stdout", "-q")
        with open(revs, "rb") as stdin, open(path, "wb") as out:
            rc, _, tail = call.run(argv, cwd=worktree, stdin=stdin, out=out)
        if rc != 0:
            raise _Refusal(_SOURCE_UNAVAILABLE, f"git pack-objects failed (exit {rc}): {tail}")
        size = os.path.getsize(path)
        if size > cap:
            raise _Refusal(
                "handoff_too_large",
                f"the pack of {start[:12]}..{head[:12]} is {size} bytes, over the {cap} cap",
            )
        return Path(path).read_bytes()

    def _store_pack(
        self, pack: bytes, start: str, head: str, context: InvocationContext
    ) -> dict[str, Any]:
        doc_id = handoff_id(context.run_id, context.step_id, head)
        digest = hashlib.sha256(pack).hexdigest()
        self._prune()
        self._store.put(
            HANDOFFS_COLLECTION,
            {
                "id": doc_id,
                "pack": base64.b64encode(pack).decode("ascii"),
                "size": len(pack),
                "sha256": digest,
                "start_sha": start,
                "head_after": head,
                "run_id": context.run_id,
                "step_id": context.step_id,
                "created_at": self._clock().isoformat(),
            },
        )
        return {"handoff_id": doc_id, "size": len(pack), "sha256": digest}

    def _prune(self) -> None:
        """Delete handoffs older than :data:`HANDOFF_TTL_S` (an import never came)."""
        cutoff = self._clock() - timedelta(seconds=HANDOFF_TTL_S)
        for doc in self._store.find(HANDOFFS_COLLECTION):
            if _expired(doc.get("created_at"), cutoff):
                self._store.delete(HANDOFFS_COLLECTION, doc["id"])


class HandoffImportPort(_Port):
    """ActorPort for the built-in ``handoff.import`` code step (module doc)."""

    def __init__(
        self,
        store: Any,
        *,
        run_as: CommandRunner | None,
        unconfigured_reason: str = "",
        blocked_reason: str = "",
        clock: Callable[[], datetime] | None = None,
        clone_url_ok: Callable[[str], bool] | None = None,
    ) -> None:
        super().__init__(
            store,
            run_as=run_as,
            unconfigured_reason=unconfigured_reason,
            blocked_reason=blocked_reason,
            clock=clock,
        )
        self._url_ok = clone_url_ok or (lambda url: _CLONE_URL_RE.fullmatch(url) is not None)

    @classmethod
    def from_env(
        cls,
        store: Any,
        environ: Mapping[str, str] | None = None,
        *,
        no_new_privs: Callable[[], bool | None] = no_new_privs,
    ) -> HandoffImportPort:
        run_as, why = run_as_from_env(environ)
        blocked = _blocked(run_as, RUN_AS_ENV, no_new_privs)
        return cls(store, run_as=run_as, unconfigured_reason=why, blocked_reason=blocked)

    def _inputs(self, input: Mapping[str, Any]) -> tuple[str, str, str, str | None, str]:
        doc_id = input.get("handoff_id")
        if not isinstance(doc_id, str) or not _ID_RE.match(doc_id):
            raise _Refusal("bad_input", "handoff_id must be a handoff.export id")
        start, head = _sha_input(input, "start_sha"), _sha_input(input, "head_after")
        base = _sha_input(input, "base_sha") if input.get("base_sha") is not None else None
        url = input.get("clone_url")
        if not isinstance(url, str) or not self._url_ok(url):
            raise _Refusal("bad_input", "clone_url must be a GitHub HTTPS clone URL")
        return doc_id, start, head, base, url

    def _step(
        self, input: Mapping[str, Any], deadline: datetime, context: InvocationContext
    ) -> dict[str, Any]:
        del context  # the import reads only its inputs and the stored handoff
        doc_id, start, head, base, url = self._inputs(input)
        runner = self._runner()
        pack = self._verified_pack(doc_id, start, head)
        tmp = tempfile.mkdtemp(prefix="culture-rules-handoff-")
        try:
            call = _Call(runner, tmp, deadline, self._clock)
            pack_path = os.path.join(tmp, "in.pack")
            Path(pack_path).write_bytes(pack)
            workspace = self._workspace(call)
            try:
                worktree = self._fill(call, workspace, url, (start, base, head), pack_path)
            except _Refusal:
                call.remove(workspace)
                raise
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self._store.delete(HANDOFFS_COLLECTION, doc_id)
        return {
            "worktree": worktree,
            "start_sha": start,
            "commit_sha": head,
            "head_before": start,
            "head_after": head,
        }

    def _verified_pack(self, doc_id: str, start: str, head: str) -> bytes:
        """The stored pack, when the document names these commits and its bytes are intact."""
        doc = self._store.get(HANDOFFS_COLLECTION, doc_id)
        if doc is None:
            raise _Refusal("handoff_missing", f"no handoff {doc_id}")
        if doc.get("start_sha") != start or doc.get("head_after") != head:
            raise _Refusal("handoff_mismatch", "the handoff names other commits than the step")
        try:
            pack = base64.b64decode(str(doc.get("pack")), validate=True)
        except ValueError as exc:
            raise _Refusal(_HANDOFF_CORRUPT, "the stored pack is not base64") from exc
        if len(pack) != doc.get("size") or hashlib.sha256(pack).hexdigest() != doc.get("sha256"):
            raise _Refusal(_HANDOFF_CORRUPT, "the stored pack's size or sha256 does not match")
        return pack

    @staticmethod
    def _workspace(call: _Call) -> str:
        """A fresh fixer-owned ``mktemp -d``; stale ones beside it are removed first."""
        out = call.ok(
            ["mktemp", "-d", "-t", f"{_WORKSPACE_PREFIX}XXXXXXXXXX"],
            cwd="/",
            code=_CHECKOUT_FAILED,
            what="mktemp -d",
        )
        path = out.strip()
        if (
            "\n" in path
            or not os.path.isabs(path)
            or os.path.normpath(path) != path
            or not os.path.basename(path).startswith(_WORKSPACE_PREFIX)
        ):
            raise _Refusal(_CHECKOUT_FAILED, "mktemp -d did not return a fresh directory")
        HandoffImportPort._prune_workspaces(call, os.path.dirname(path), path)
        return path

    @staticmethod
    def _prune_workspaces(call: _Call, parent: str, keep: str) -> None:
        """Best effort: remove this account's handoff workspaces older than the TTL."""
        argv = [
            "find",
            parent,
            "-mindepth",
            "1",
            "-maxdepth",
            "1",
            "-type",
            "d",
            "-name",
            f"{_WORKSPACE_PREFIX}*",
            "!",
            "-path",
            keep,
            "-mmin",
            f"+{WORKTREE_TTL_MIN}",
            "-exec",
            "rm",
            "-rf",
            "--",
            "{}",
            "+",
        ]
        rc, _, tail = call.run(argv, cwd="/")
        if rc != 0:
            log.warning("handoff: pruning old workspaces failed (exit %s): %s", rc, tail)

    @staticmethod
    def _fill(
        call: _Call,
        workspace: str,
        url: str,
        shas: tuple[str, str | None, str],
        pack_path: str,
    ) -> str:
        """Build the worktree at ``workspace/repo``: fetch the base commits, index the pack,
        check ``head_after`` out. Returns the worktree's path."""
        start, base, head = shas
        call.ok(
            _hard_git("init", "--quiet", "--template=", _REPO_DIR),
            cwd=workspace,
            code=_CHECKOUT_FAILED,
            what="git init",
        )
        worktree = os.path.join(workspace, _REPO_DIR)
        call.ok(
            _hard_git("remote", "add", "origin", url),
            cwd=worktree,
            code=_CHECKOUT_FAILED,
            what="git remote add",
        )
        wanted = [start] + ([base] if base and base != start else [])
        fetch = ["fetch", "--quiet", "--no-tags", "--no-write-fetch-head", "origin", *wanted]
        call.ok(
            _hard_git(*fetch),
            cwd=worktree,
            code="handoff_fetch_failed",
            what="git fetch of start_sha",
            retryable=True,
        )
        with open(pack_path, "rb") as pack:
            call.ok(
                _hard_git("index-pack", "--stdin", "--strict", "--fix-thin"),
                cwd=worktree,
                code=_HANDOFF_CORRUPT,
                what="git index-pack",
                stdin=pack,
            )
        HandoffImportPort._check_out(call, worktree, start, head)
        return worktree

    @staticmethod
    def _check_out(call: _Call, worktree: str, start: str, head: str) -> None:
        rc, _, _ = call.run(_hard_git("cat-file", "-e", f"{head}^{{commit}}"), cwd=worktree)
        if rc != 0:
            raise _Refusal(_HANDOFF_CORRUPT, f"the pack does not hold head_after {head[:12]}")
        _descends(call, worktree, start, head, _HANDOFF_CORRUPT)
        call.ok(
            _hard_git("-c", "advice.detachedHead=false", "checkout", "--quiet", "--detach", head),
            cwd=worktree,
            code=_CHECKOUT_FAILED,
            what="git checkout",
        )
        actual = call.ok(
            _hard_git("rev-parse", "--verify", _HEAD_COMMIT),
            cwd=worktree,
            code=_CHECKOUT_FAILED,
            what="git rev-parse",
        ).strip()
        if actual != head:
            raise _Refusal(_CHECKOUT_FAILED, f"the checkout's HEAD is {actual[:12]}")
