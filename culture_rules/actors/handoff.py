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
run_id, step_id, created_at, expires_at, rev}`` and the step outputs ``handoff_id``,
``size`` and ``sha256``. The id is derived from the run, the step and ``head_after``, so a
re-run writes its own document. ``rev`` changes on every write (each write to an existing
hand-off is a compare-and-set on it); ``expires_at`` is a date :data:`HANDOFF_TTL_S` after
the last write that renewed it (the export, an import's claim, its receipt).

An export never erases a claim or a receipt (Codex round 3 #3). A hand-off that already
names the same ``start_sha`` and ``head_after`` is an export replay: only its ``rev`` and
``expires_at`` are renewed and the step reports the stored pack's ``size`` and ``sha256``
(git object ids are content hashes, so any pack of those commits carries the same objects).
One naming other commits is replaced while no import has claimed it, and otherwise refused
``handoff_conflict`` (non-retryable).

Retention (Codex round 2 #4): where the store has indexes (MongoDB) the hand-offs carry a
TTL index on ``expires_at`` (:data:`EXPIRY_INDEX`), so the server deletes an expired one on
its own. Each export and each import also prunes (:func:`prune_handoffs`): a bounded query
(at most :data:`PRUNE_BATCH`) that selects only the ``id`` and ``rev`` of hand-offs whose
``expires_at`` has passed - never a pack body - and deletes each in one store transaction
that reads it again and deletes it only while its ``rev`` is the one the query saw (Codex
round 3 #4), so one an import claimed or an export renewed meanwhile stays.

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
no ``worktree`` output, so the gate never judges a half-built tree.

The import is claimed before anything is built (Codex round 2 #2/#3): a compare-and-set on
the ``rev`` it read sets ``import_key`` (the invocation's idempotency key) and
``import_state: importing`` and renews ``expires_at``, so no prune takes the hand-off while
the import builds. A hand-off another invocation claimed or imported is refused
``handoff_already_imported`` before any command runs. On success the import receipt
(``imported: {idempotency_key, worktree, at}``, ``import_state: imported``) is written by
compare-and-set on the claim's ``rev``, and only then does the step output ``worktree``,
``start_sha`` and ``commit_sha`` (the gate's inputs) plus ``head_before`` and ``head_after``
(the agent step's own names). A lost receipt write reads the hand-off again: this
invocation's own receipt naming another worktree (a concurrent replay) is reported instead
while that worktree is intact, and overwritten with this one's otherwise (Codex round 3
#2); a hand-off with no claim (it was pruned and exported again) is claimed again;
another invocation's claim is ``handoff_already_imported`` and a hand-off gone
``handoff_receipt_lost`` (non-retryable) - each after removing the workspace built here.
The pack is kept until the hand-off expires: a replay with the same idempotency key (a lost
acknowledgement) passes the same checks, then returns the receipt's worktree while its
``HEAD`` is still ``head_after`` with nothing else in it, else removes it and rebuilds one
from the pack. The gate then judges exactly the commit the Codex fixer made: git object
ids are content hashes, and ``index-pack --strict`` verifies every one.
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
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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
from culture_rules.store.port import TransientStoreError
from culture_rules.store.retry import run_transaction

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_BYTES",
    "EXPIRY_INDEX",
    "EXPORT_BUILTIN",
    "HANDOFFS_COLLECTION",
    "HANDOFF_RUN_AS_ENV",
    "HANDOFF_TTL_S",
    "IMPORT_BUILTIN",
    "MAX_MAX_BYTES",
    "PRUNE_BATCH",
    "WORKTREE_TTL_MIN",
    "HandoffExportPort",
    "HandoffImportPort",
    "ensure_handoff_expiry",
    "handoff_id",
    "handoff_run_as_from_env",
    "prune_handoffs",
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
_MISSING = "handoff_missing"
_ALREADY_IMPORTED = "handoff_already_imported"
_RECEIPT_LOST = "handoff_receipt_lost"
_EXPIRES_AT = "expires_at"
EXPIRY_INDEX = "handoffs_expiry"
"""The MongoDB TTL index on ``expires_at`` (a date) that deletes an expired hand-off."""
PRUNE_BATCH = 100
"""At most this many expired hand-offs are pruned by one export or import."""
_REV, _IMPORT_KEY, _IMPORT_STATE, _RECEIPT, _WORKTREE = (
    "rev",
    "import_key",
    "import_state",
    "imported",
    "worktree",
)
_PRUNE_FIELDS = ("id", _REV)  # what the prune reads of an expired hand-off: never its pack
_CLAIM_TRIES = 8
_IMPORTING, _IMPORTED = "importing", _RECEIPT
_CONFLICT = "handoff_conflict"
_START_SHA, _HEAD_AFTER = "start_sha", "head_after"
_COMMITS = (_START_SHA, _HEAD_AFTER)  # what one hand-off carries: its identity
_CONTENTION = "handoff_contention"


def handoff_id(run_id: str, step_id: str, head_after: str) -> str:
    """The deterministic document id of one export (a re-run writes its own)."""
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


def _new_rev() -> str:
    return uuid.uuid4().hex


def ensure_handoff_expiry(store: Any) -> None:
    """Create :data:`EXPIRY_INDEX` (a TTL index on ``expires_at``) where the store supports
    indexes (MongoDB): the server then deletes an expired hand-off on its own."""
    ensure = getattr(store, "ensure_index", None)
    if callable(ensure):
        ensure(HANDOFFS_COLLECTION, [(_EXPIRES_AT, 1)], name=EXPIRY_INDEX, ttl_seconds=0)


def prune_handoffs(store: Any, now: datetime) -> int:
    """Codex round 2 #4, round 3 #4: delete up to :data:`PRUNE_BATCH` hand-offs whose
    ``expires_at`` has passed; how many. The query selects only ``id`` and ``rev`` of the
    expired ones (on MongoDB through the TTL index's field), never a pack body. Each is
    deleted in **one store transaction** that reads it again and deletes it only while its
    ``rev`` is still the one the query saw. Every write to a hand-off (an export, an
    import's claim or receipt) changes its ``rev``, so one renewed, claimed or exported
    again since the query is kept: either that write committed before the transaction's
    read (the ``rev`` differs, nothing is deleted), or after it (MongoDB fails the
    transaction's delete with a write conflict and the re-run reads the new ``rev``; the
    MemoryStore serialises transactions). There is no intermediate state: a hand-off is
    there, or gone. A delete that keeps conflicting is left to the next prune (or the TTL
    index). A store without ranged reads prunes nothing here (its TTL index, if any,
    does)."""
    ranged = getattr(store, "find_range", None)
    if not callable(ranged):
        return 0
    expired = ranged(
        HANDOFFS_COLLECTION,
        None,
        field=_EXPIRES_AT,
        upto=now,
        limit=PRUNE_BATCH,
        fields=_PRUNE_FIELDS,
    )
    pruned = 0
    for doc in expired:
        try:
            pruned += run_transaction(store, _deleter(doc["id"], doc.get(_REV)))
        except TransientStoreError as exc:
            log.warning("handoff: pruning %s kept conflicting: %s", doc["id"], exc)
    return pruned


def _deleter(doc_id: str, rev: Any) -> Callable[[Any], int]:
    """The prune's transaction body: delete ``doc_id`` iff its ``rev`` is still ``rev``."""

    def body(tx: Any) -> int:
        current = tx.get(HANDOFFS_COLLECTION, doc_id)
        if current is None or current.get(_REV) != rev:
            return 0
        return int(tx.delete(HANDOFFS_COLLECTION, doc_id))

    return body


def _own_worktree(path: str) -> bool:
    """Whether ``path`` has the shape of a worktree an import built:
    ``<tmp>/culture_rules_handoff.*/repo``, absolute and normalised."""
    return (
        os.path.isabs(path)
        and "\n" not in path
        and "\x00" not in path
        and os.path.normpath(path) == path
        and os.path.basename(path) == _REPO_DIR
        and os.path.basename(os.path.dirname(path)).startswith(_WORKSPACE_PREFIX)
    )


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


def _taken(doc_id: str) -> _Refusal:
    return _Refusal(_ALREADY_IMPORTED, f"handoff {doc_id} was imported by another step")


def _receipt(doc: Mapping[str, Any]) -> Mapping[str, Any] | None:
    receipt = doc.get(_RECEIPT)
    return receipt if isinstance(receipt, Mapping) else None


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
        ensure_handoff_expiry(store)

    def _expiry(self) -> datetime:
        """When a hand-off written now expires: :data:`HANDOFF_TTL_S` on (a date)."""
        return self._clock() + timedelta(seconds=HANDOFF_TTL_S)

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        try:
            return InvocationResult.completed(self._step(input, deadline, context, idempotency_key))
        except _Refusal as exc:
            return InvocationResult.failed(exc.message(), retryable=exc.retryable)

    def _step(
        self, input: Mapping[str, Any], deadline: datetime, context: InvocationContext, ik: str
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
        self, input: Mapping[str, Any], deadline: datetime, context: InvocationContext, ik: str
    ) -> dict[str, Any]:
        del ik  # a re-run writes its own document (the id is derived from the step)
        worktree = _abs_path(input, _WORKTREE)
        start, head = _sha_input(input, _START_SHA), _sha_input(input, _HEAD_AFTER)
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
        """Codex round 3 #3: write the hand-off **without ever erasing a claim or a
        receipt**, each write a compare-and-set on the ``rev`` read (an absent document:
        an insert that loses to a concurrent one). An existing hand-off of the same commits
        (``start_sha`` and ``head_after``; git object ids are content hashes, so any pack of
        them carries the same objects) is an export replay: only its ``rev`` and retention
        are renewed, its pack, claim and receipt kept, and the step reports the stored pack.
        One of other commits replaces an unclaimed hand-off and refuses a claimed one
        ``handoff_conflict`` (non-retryable: an import already holds what it names)."""
        doc_id = handoff_id(context.run_id, context.step_id, head)
        prune_handoffs(self._store, self._clock())
        fresh = {
            "pack": base64.b64encode(pack).decode("ascii"),
            "size": len(pack),
            "sha256": hashlib.sha256(pack).hexdigest(),
            _START_SHA: start,
            _HEAD_AFTER: head,
            "run_id": context.run_id,
            "step_id": context.step_id,
            "created_at": self._clock().isoformat(),
            _IMPORT_KEY: None,
            _IMPORT_STATE: None,
            _RECEIPT: None,
        }
        for _ in range(_CLAIM_TRIES):
            current = self._store.get(HANDOFFS_COLLECTION, doc_id)
            changes = self._export_changes(doc_id, current, fresh)
            expected = {_REV: (current or {}).get(_REV)}
            res = self._store.update_if(
                HANDOFFS_COLLECTION, doc_id, expected, changes, upsert=current is None
            )
            if res.won and res.document is not None:
                doc = res.document
                return {"handoff_id": doc_id, "size": doc["size"], "sha256": doc["sha256"]}
        raise _Refusal(_CONTENTION, f"handoff {doc_id} kept changing", retryable=True)

    def _export_changes(
        self, doc_id: str, current: Mapping[str, Any] | None, fresh: Mapping[str, Any]
    ) -> dict[str, Any]:
        """What one export writes over ``current`` (module doc, Codex round 3 #3)."""
        renewal = {_REV: _new_rev(), _EXPIRES_AT: self._expiry()}
        if current is None:
            return {**fresh, **renewal}
        same = all(current.get(k) == fresh[k] for k in _COMMITS)
        if same:
            return renewal
        if current.get(_IMPORT_KEY) is not None:
            raise _Refusal(
                _CONFLICT,
                f"handoff {doc_id} names other commits and an import already claimed it",
            )
        return {**fresh, **renewal}


@dataclass(frozen=True)
class _Want:
    """The hand-off one import asks for: its document id and commits."""

    doc_id: str
    start: str
    head: str

    def guard(self, rev: Any) -> dict[str, Any]:
        """The compare-and-set guard: the document as last read (``rev``), still naming
        these commits."""
        return {_REV: rev, _START_SHA: self.start, _HEAD_AFTER: self.head}


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
        start, head = _sha_input(input, _START_SHA), _sha_input(input, _HEAD_AFTER)
        base = _sha_input(input, "base_sha") if input.get("base_sha") is not None else None
        url = input.get("clone_url")
        if not isinstance(url, str) or not self._url_ok(url):
            raise _Refusal("bad_input", "clone_url must be a GitHub HTTPS clone URL")
        return doc_id, start, head, base, url

    def _step(
        self, input: Mapping[str, Any], deadline: datetime, context: InvocationContext, ik: str
    ) -> dict[str, Any]:
        del context  # the import reads only its inputs and the stored handoff
        doc_id, start, head, base, url = self._inputs(input)
        runner = self._runner()
        prune_handoffs(self._store, self._clock())  # #4: retention is bounded on import too
        want = _Want(doc_id, start, head)
        doc, pack = self._verified_pack(doc_id, start, head)
        rev, receipt = self._claim(want, doc, ik)
        tmp = tempfile.mkdtemp(prefix="culture-rules-handoff-")
        try:
            call = _Call(runner, tmp, deadline, self._clock)
            worktree = self._intact(call, receipt, head)
            if worktree is None:
                worktree = self._build(call, tmp, pack, url, (start, base, head))
            worktree = self._record(call, want, rev, ik, worktree)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return {
            _WORKTREE: worktree,
            _START_SHA: start,
            "commit_sha": head,
            "head_before": start,
            _HEAD_AFTER: head,
        }

    def _claim(
        self, want: _Want, doc: Mapping[str, Any], ik: str
    ) -> tuple[str, Mapping[str, Any] | None]:
        """Codex round 2 #2/#3: claim the hand-off for this invocation **before** anything
        is built - a compare-and-set on the ``rev`` it was read with - and renew its
        retention (``expires_at``) so no prune takes it while the import builds. Another
        invocation's claim or receipt refuses ``handoff_already_imported``; this
        invocation's own (a replay) is renewed. Returns the claim's ``rev`` and this
        invocation's earlier receipt, if any."""
        for _ in range(_CLAIM_TRIES):
            holder = doc.get(_IMPORT_KEY)
            if holder is not None and holder != ik:
                raise _taken(want.doc_id)
            rev = _new_rev()
            changes: dict[str, Any] = {_REV: rev, _EXPIRES_AT: self._expiry()}
            if holder is None:
                changes.update({_IMPORT_KEY: ik, _IMPORT_STATE: _IMPORTING, _RECEIPT: None})
            res = self._store.update_if(
                HANDOFFS_COLLECTION, want.doc_id, want.guard(doc.get(_REV)), changes
            )
            if res.won:
                return rev, _receipt(doc) if holder == ik else None
            current = self._store.get(HANDOFFS_COLLECTION, want.doc_id)
            if current is None:
                raise _Refusal(_MISSING, f"no handoff {want.doc_id}")
            doc = current
        raise _Refusal(_CONTENTION, f"handoff {want.doc_id} kept changing", retryable=True)

    def _record(self, call: _Call, want: _Want, rev: str, ik: str, worktree: str) -> str:
        """Codex round 1 #2, round 2 #2/#3: write the import receipt (``imported:
        {idempotency_key, worktree, at}``) by compare-and-set on the claim's ``rev``; the
        step completes only once it is durably recorded. On a lost compare-and-set the
        document is read again: this invocation's own receipt naming another, intact
        worktree (a concurrent replay won) is returned instead, a stale one overwritten
        (Codex round 3 #2); a renewed claim (an export replay, a same-key replay) is
        written again on its new ``rev``; a document without a claim (it was pruned and
        exported again) is claimed again; another invocation's claim fails
        ``handoff_already_imported`` and a document gone (pruned) ``handoff_receipt_lost``
        (non-retryable: a replay would find nothing to import) - each after removing the
        worktree built here. Returns the worktree the step reports."""
        receipt = {"idempotency_key": ik, _WORKTREE: worktree, "at": self._clock().isoformat()}
        for _ in range(_CLAIM_TRIES):
            changes = {
                _REV: _new_rev(),
                _IMPORT_KEY: ik,
                _IMPORT_STATE: _IMPORTED,
                _RECEIPT: receipt,
                _EXPIRES_AT: self._expiry(),
            }
            if self._store.update_if(
                HANDOFFS_COLLECTION, want.doc_id, want.guard(rev), changes
            ).won:
                return worktree
            doc = self._store.get(HANDOFFS_COLLECTION, want.doc_id)
            theirs = self._settled(call, want, doc, ik, worktree)
            if theirs is not None:
                return theirs
            rev = doc.get(_REV) if doc is not None else rev
        self._discard(call, worktree)
        raise _Refusal(_RECEIPT_LOST, f"the import receipt of {want.doc_id} kept losing")

    def _settled(
        self,
        call: _Call,
        want: _Want,
        doc: Mapping[str, Any] | None,
        ik: str,
        worktree: str,
    ) -> str | None:
        """After a lost receipt write: the worktree to report when this invocation's own
        receipt already stands, None to write again; raises (removing ``worktree``) when the
        hand-off is gone or another invocation holds it. A competing same-key receipt is
        taken only when its worktree is intact; it is never removed here (the replay that
        found it changed already removed it, or the workspace TTL will)."""
        if doc is None:
            self._discard(call, worktree)
            raise _Refusal(_RECEIPT_LOST, f"handoff {want.doc_id} was pruned during the import")
        holder = doc.get(_IMPORT_KEY)
        if holder is not None and holder != ik:
            self._discard(call, worktree)
            raise _taken(want.doc_id)
        mine = _receipt(doc) if doc.get(_IMPORT_STATE) == _IMPORTED else None
        path = (mine or {}).get(_WORKTREE)
        if holder != ik or not isinstance(path, str):
            return None
        if path == worktree:
            return path
        # Codex round 3 #2: a same-key receipt for another worktree is reported only while
        # that worktree is intact; a stale one (an earlier receipt whose worktree a replay
        # removed or found changed, left standing by a replay that renewed the claim and
        # stopped) is overwritten with the one built here, by compare-and-set on its rev.
        if self._valid(call, path, want.head):
            self._discard(call, worktree)
            return path
        return None

    @staticmethod
    def _discard(call: _Call, worktree: str) -> None:
        """Remove the workspace of a worktree this import will not report."""
        if _own_worktree(worktree):
            call.remove(os.path.dirname(worktree))

    def _build(
        self,
        call: _Call,
        tmp: str,
        pack: bytes,
        url: str,
        shas: tuple[str, str | None, str],
    ) -> str:
        """A fresh worktree at ``head_after`` from the pack (removed again on a refusal)."""
        pack_path = os.path.join(tmp, "in.pack")
        Path(pack_path).write_bytes(pack)
        workspace = self._workspace(call)
        try:
            return self._fill(call, workspace, url, shas, pack_path)
        except _Refusal:
            call.remove(workspace)
            raise

    @staticmethod
    def _intact(call: _Call, receipt: Mapping[str, Any] | None, head: str) -> str | None:
        """Codex round 1 #2: the worktree a receipt names, when this invocation already
        built it (a replay after a lost acknowledgement) and it is still exactly
        ``head_after`` with nothing else in it; None otherwise, and a changed one is removed
        (the replay builds a fresh one from the retained pack)."""
        path = (receipt or {}).get(_WORKTREE)
        if not isinstance(path, str) or not _own_worktree(path):
            return None
        if HandoffImportPort._valid(call, path, head):
            return path
        call.remove(os.path.dirname(path))
        return None

    @staticmethod
    def _valid(call: _Call, path: str, head: str) -> bool:
        """Whether ``path`` is a worktree an import built, still exactly ``head`` with
        nothing else in it (missing, moved or dirty: False)."""
        if not _own_worktree(path):
            return False
        rc, out, _ = call.run(_hard_git("-C", path, "rev-parse", "--verify", _HEAD_COMMIT), cwd="/")
        if rc != 0 or out.decode("utf-8", errors="replace").strip() != head:
            return False
        status = ("status", "--porcelain", "--untracked-files=all", "--ignored")
        rc, out, _ = call.run(_hard_git("-C", path, *status), cwd="/")
        return rc == 0 and not out.strip()

    def _verified_pack(self, doc_id: str, start: str, head: str) -> tuple[Mapping[str, Any], bytes]:
        """The stored document and its pack, when the document names these commits and its
        bytes are intact."""
        doc = self._store.get(HANDOFFS_COLLECTION, doc_id)
        if doc is None:
            raise _Refusal(_MISSING, f"no handoff {doc_id}")
        if doc.get(_START_SHA) != start or doc.get(_HEAD_AFTER) != head:
            raise _Refusal("handoff_mismatch", "the handoff names other commits than the step")
        try:
            pack = base64.b64decode(str(doc.get("pack")), validate=True)
        except ValueError as exc:
            raise _Refusal(_HANDOFF_CORRUPT, "the stored pack is not base64") from exc
        if len(pack) != doc.get("size") or hashlib.sha256(pack).hexdigest() != doc.get("sha256"):
            raise _Refusal(_HANDOFF_CORRUPT, "the stored pack's size or sha256 does not match")
        return doc, pack

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
