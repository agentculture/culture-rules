"""``github.pr_open`` action port: commit files to a new branch and open a PR as the GitHub App.

A generic writer: which repo, branch, files, title, body and labels is step configuration (the
files usually come from an earlier step's output). It reuses
:class:`~culture_rules.extensions.github.port.GitHubCommentPort`'s actor resolution (a stored,
enabled actor with ``params.surface == "github"``), its repo allowlist, its per-actor App cache
and its deadline-bounded resolve.

Params: ``actor``, ``repo``, ``branch`` (the new branch), ``base`` (optional; the repo's
default branch when absent or empty), ``files`` (a list of ``{path, content}``, UTF-8 text, at most
:data:`MAX_FILES` files and :data:`MAX_TOTAL_BYTES` bytes in all), ``title``, ``body``
(optional) and ``labels`` (optional, at most :data:`MAX_LABELS` names).

In order, refusing at the first failed check; every check before step 4 reads only the store
and the input, so a refusal reads no secret and makes no network call:

1. the actor (``actor_not_found``), the allowlist (``repo_not_allowed``,
   ``actor_misconfigured``), and the actor listing this kind in ``params.actions``
   (:data:`NOT_DECLARED`, ``action_not_declared``): an App writes code only where its
   configuration says it may;
2. the input: shapes (``bad_input``), the limits (``too_many_files``, ``files_too_large``),
   each path (``path_refused``: relative, no empty, ``.`` or ``..`` segment, no ``.git``
   segment, nothing under ``.github/workflows/``, no control characters) and a ``branch``
   equal to ``base`` (``branch_is_base``);
3. the App (``secret_unavailable``; ``deadline_exceeded`` / ``lookup_busy`` retryable);
4. the installation token, then the once claim (below), then the repo read: a ``branch``
   equal to the repo's default branch is refused whatever the base (``branch_is_default``,
   compared without case), so it never commits to the default branch;
5. an existing ``branch`` opens nothing: the step completes with ``skipped: branch_exists``
   and the number and link of the open PR whose head is that branch (``None`` when none is
   open);
6. a tree of the files on top of the base tip's tree, a commit of it whose one parent is the
   base tip, then the **new** branch at that commit (GitHub refuses an existing ref with 422,
   read as step 5; there is no call that moves or force-updates a ref), then the pull and its
   labels. A label failure does not undo the PR: it is reported in ``labels_error``.

Outputs: ``number``, ``url``, ``head_sha`` (the branch's commit), ``branch``, ``base`` and
``labels_error`` (an error code, or ``None``); a skip or a repeat adds ``skipped``.

A retried step opens no second PR. The port claims ``sha256("github.pr_open#" + key)`` (the
engine's idempotency key: one run and step, the same on every attempt) by insert in
:data:`PR_OPEN_ONCE_COLLECTION` before it writes, and records the commit before it creates
the branch, so ``supports_idempotency_key`` is True. A later attempt that finds the claim:

* ``opened``: completes with the recorded outputs (``skipped: opened_before``);
* otherwise it takes the claim over (below), then reads first: a PR from the branch that is
  open, or closed or merged with its head at the recorded commit (an older PR that only
  reused the branch name is not taken), completes it (``skipped: opened_unconfirmed``, a lost
  answer; only an open one is labelled), even when the branch was deleted since; the branch
  at the recorded commit opens the pull (a failure after the push); no branch starts over
  (nothing of it landed); the branch at another commit is step 5.

The claim is exclusive. Each attempt holds it with its own random ``owner`` token; a later
attempt takes it over only by compare-and-set on the owner and state it read, and every
record and release of an attempt is conditional on that attempt still owning it (a release
is a transaction that deletes only its own claim). An attempt that finds it no longer owns
the claim writes nothing more, to the claim or to GitHub, and fails :data:`CLAIM_LOST`
(retryable: the retry reads the claim), with one exception: GitHub makes one branch, so an
attempt whose branch creation GitHub accepted after another attempt took the claim takes it
back by compare-and-set (the claim follows the branch) unless that other attempt already
carries the same commit on. A taken-over attempt whose branch creation meets an existing
branch keeps the claim and fails :data:`CLAIM_LOST`, since that branch may be an earlier
attempt's.

GitHub also refuses a second open PR for the same head and base. A failure before the branch
exists (only unreferenced git objects were made) releases the claim; any later one keeps it.
The claim holds the repo, branch, base, commit, owner token and outputs, never the files'
contents.
Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import COMPLETED, InvocationContext, InvocationResult
from culture_rules.extensions.github.client import GitHubApp, GitHubError, is_branch_name
from culture_rules.extensions.github.port import (
    ACTOR_NOT_FOUND,
    BAD_INPUT,
    SECRET_UNAVAILABLE,
    GitHubCommentPort,
    _refused,
    repo_refusal,
)
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.port import DuplicateKeyError
from culture_rules.store.retry import run_transaction

__all__ = [
    "CLAIM_LOST",
    "KIND",
    "MAX_FILES",
    "MAX_LABELS",
    "MAX_TOTAL_BYTES",
    "NOT_DECLARED",
    "PR_OPEN_ONCE_COLLECTION",
    "GitHubPrOpenPort",
    "path_refusal",
]

log = logging.getLogger(__name__)

KIND = "github.pr_open"
PR_OPEN_ONCE_COLLECTION = "github_pr_open_once"
"""Claims of ``github.pr_open`` steps (one document per run and step)."""
CLAIM_LOST = "claim_lost"
"""Another attempt of the same step holds the claim now (retryable: a retry reads it)."""
NOT_DECLARED = "action_not_declared"
"""The actor does not list ``github.pr_open`` in its ``params.actions``."""

MAX_FILES = 50
"""The most files one step commits."""
MAX_TOTAL_BYTES = 1_048_576
"""The most bytes (UTF-8) of file content one step commits, all files together (1 MiB)."""
MAX_PATH_CHARS = 1024
MAX_TITLE_CHARS = 256
MAX_BODY_CHARS = 65_536
"""GitHub's own limit on a PR body."""
MAX_LABELS = 20
MAX_LABEL_CHARS = 50
"""GitHub's own limit on a label name."""

_CLAIMED, _PUSHED, _OPENED, _UNKNOWN = "claimed", "pushed", "opened", "unknown"
_OUTPUTS = ("number", "url", "head_sha", "branch", "base", "labels_error")


@dataclass(frozen=True)
class _Request:
    repo: str
    branch: str
    base: str | None
    files: list[tuple[str, str]]
    title: str
    body: str
    labels: list[str]


def path_refusal(path: Any) -> bool:
    """Whether a file ``path`` is refused (``path_refused``): not a relative POSIX path of
    plain segments, a ``.git`` segment, anything under ``.github/workflows/`` (an App without
    the Workflows permission cannot write it, and a workflow is code that runs with the repo's
    secrets) or a control character."""
    if not isinstance(path, str) or not 0 < len(path) <= MAX_PATH_CHARS:
        return True
    if path.startswith("/") or "\\" in path or any(ord(c) < 32 or ord(c) == 127 for c in path):
        return True
    parts = path.split("/")
    if any(part in ("", ".", "..") or part.lower() == ".git" for part in parts):
        return True
    return [p.lower() for p in parts[:2]] == [".github", "workflows"]


def _files(value: Any) -> tuple[list[tuple[str, str]] | None, str | None]:
    """``(files, None)`` or ``(None, refusal)``."""
    if not isinstance(value, list) or not value:
        return None, BAD_INPUT
    if len(value) > MAX_FILES:
        return None, "too_many_files"
    out: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, Mapping):
            return None, BAD_INPUT
        path, content = item.get("path"), item.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            return None, BAD_INPUT
        if path_refusal(path):
            return None, "path_refused"
        out.append((path, content))
    if len({p for p, _ in out}) != len(out):
        return None, BAD_INPUT
    if sum(len(c.encode("utf-8")) for _, c in out) > MAX_TOTAL_BYTES:
        return None, "files_too_large"
    return out, None


def _labels(value: Any) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_LABELS:
        return None
    if not all(isinstance(v, str) and 0 < len(v) <= MAX_LABEL_CHARS for v in value):
        return None
    return list(value)


def _request(input: Mapping[str, Any]) -> tuple[_Request | None, str | None]:
    """The checked input (module doc, step 2), or the refusal."""
    branch, base = input.get("branch"), input.get("base")
    if base == "":  # empty means not given: the repo's default branch
        base = None
    title, body = input.get("title"), input.get("body")
    if not is_branch_name(branch) or (base is not None and not is_branch_name(base)):
        return None, BAD_INPUT
    if not isinstance(title, str) or not 0 < len(title.strip()) <= MAX_TITLE_CHARS:
        return None, BAD_INPUT
    if len(title) > MAX_TITLE_CHARS:
        return None, BAD_INPUT
    if body is not None and (not isinstance(body, str) or len(body) > MAX_BODY_CHARS):
        return None, BAD_INPUT
    labels = _labels(input.get("labels"))
    if labels is None:
        return None, BAD_INPUT
    files, refusal = _files(input.get("files"))
    if files is None:
        return None, refusal
    if base is not None and branch.casefold() == base.casefold():
        return None, "branch_is_base"
    return _Request(str(input["repo"]), branch, base, files, title, body or "", labels), None


def _declares(doc: Mapping[str, Any]) -> bool:
    actions = (doc.get("params") or {}).get("actions")
    return isinstance(actions, list) and KIND in actions


class GitHubPrOpenPort(GitHubCommentPort):
    """ActorPort for ``github.pr_open`` (see the module docstring)."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        actor_id = context.actor or input.get("actor")
        snapshot = self._store.get(ACTORS_COLLECTION, actor_id) if actor_id else None
        conn = self._connection_of(snapshot)
        if conn is None:
            self._apps.pop(str(actor_id), None)
            return InvocationResult.failed(ACTOR_NOT_FOUND, retryable=False)
        allowed, refusal = repo_refusal(conn, input.get("repo"))
        if not refusal and not _declares(snapshot):
            refusal = NOT_DECLARED
        if not refusal and (not isinstance(idempotency_key, str) or not idempotency_key):
            refusal = BAD_INPUT
        request = None
        if not refusal:
            request, refusal = _request(input)
        if refusal or request is None:
            log.info("github.pr_open refused: %s", refusal)
            return InvocationResult.failed(refusal or BAD_INPUT, retryable=False)
        try:
            app = self._app_within(str(actor_id), conn, allowed, deadline)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        if app is None:
            return InvocationResult.failed(SECRET_UNAVAILABLE, retryable=False)
        try:
            with app.deadline(deadline):
                return self._open_once(app, request, idempotency_key)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)

    # ------------------------------------------------------------------------- the claim

    def _open_once(self, app: GitHubApp, req: _Request, key: str) -> InvocationResult:
        app.installation_token()  # before the claim: a token failure claims nothing
        claim_id = hashlib.sha256(f"{KIND}#{key}".encode()).hexdigest()
        owner = secrets.token_hex(16)  # this attempt's hold on the claim
        claim = {
            "id": claim_id,
            "repo": req.repo,
            "branch": req.branch,
            "base": None,
            "commit_sha": None,
            "state": _CLAIMED,
            "owner": owner,
        }
        try:
            self._store.insert(PR_OPEN_ONCE_COLLECTION, claim)
        except DuplicateKeyError:
            return self._prior(app, req, claim_id, owner)
        return self._open(app, req, claim_id, owner, first=True)

    def _release(self, claim_id: str, owner: str) -> bool:
        """Delete the claim iff this attempt still owns it (one transaction); whether it did."""

        def drop(tx: Any) -> bool:
            current = tx.get(PR_OPEN_ONCE_COLLECTION, claim_id)
            if current is None or current.get("owner") != owner:
                return False
            tx.delete(PR_OPEN_ONCE_COLLECTION, claim_id)
            return True

        return run_transaction(self._store, drop)

    def _record(self, claim_id: str, owner: str, changes: Mapping[str, Any]) -> bool:
        """Set ``changes`` on the claim iff this attempt still owns it; whether it did."""
        return self._store.update_if(
            PR_OPEN_ONCE_COLLECTION, claim_id, {"owner": owner}, dict(changes)
        ).won

    def _take_over(self, claim_id: str, prior: Mapping[str, Any], owner: str) -> bool:
        """Take the claim from the attempt that held it when it was read (compare-and-set)."""
        if not prior:
            return False
        expected = {"owner": prior.get("owner"), "state": prior.get("state")}
        return self._store.update_if(
            PR_OPEN_ONCE_COLLECTION, claim_id, expected, {"owner": owner}
        ).won

    def _follow_branch(
        self, claim_id: str, owner: str, req: _Request, base: str, commit: str
    ) -> bool:
        """This attempt's branch landed at ``commit`` after another attempt took the claim:
        GitHub made one branch, so the claim follows it (compare-and-set on what was read).
        False when another attempt already carries this commit on, or the claim is opened."""
        changes = {"owner": owner, "base": base, "commit_sha": commit, "state": _PUSHED}
        for _ in range(3):
            current = self._store.get(PR_OPEN_ONCE_COLLECTION, claim_id)
            if current is None:  # released by an attempt that saw a branch not its own
                doc = {"id": claim_id, "repo": req.repo, "branch": req.branch, **changes}
                try:
                    self._store.insert(PR_OPEN_ONCE_COLLECTION, doc)
                    return True
                except DuplicateKeyError:
                    continue
            if current.get("state") == _OPENED or current.get("commit_sha") == commit:
                return False
            expected = {k: current.get(k) for k in ("owner", "state", "commit_sha")}
            if self._store.update_if(PR_OPEN_ONCE_COLLECTION, claim_id, expected, changes).won:
                return True
        return False

    @staticmethod
    def _lost() -> InvocationResult:
        log.info("github.pr_open: another attempt of this step holds the claim")
        return InvocationResult.failed(CLAIM_LOST, retryable=True)

    def _prior(self, app: GitHubApp, req: _Request, claim_id: str, owner: str) -> InvocationResult:
        """An earlier attempt of this step claimed it: never write blindly (module doc)."""
        prior = self._store.get(PR_OPEN_ONCE_COLLECTION, claim_id) or {}
        if prior.get("state") != _OPENED and not self._take_over(claim_id, prior, owner):
            prior = self._store.get(PR_OPEN_ONCE_COLLECTION, claim_id) or {}
            if prior.get("state") != _OPENED:
                return self._lost()
        if prior.get("state") == _OPENED:
            done = {k: prior.get(k) for k in _OUTPUTS}
            return InvocationResult.completed({**done, "skipped": "opened_before"})
        base = prior.get("base") if isinstance(prior.get("base"), str) else None
        commit = prior.get("commit_sha")
        pull = app.pull_for(req.repo, req.branch, commit)
        head = app.branch_head(req.repo, req.branch)
        if pull is not None:  # open, or closed/merged at this claim's commit: a lost answer
            sha = head if pull.get("open") else commit
            out = self._opened(app, req, claim_id, owner, pull, sha, base or req.base)
            return InvocationResult.completed({**out, "skipped": "opened_unconfirmed"})
        if head is None:  # nothing of it landed: start over under the same claim
            restart = {"state": _CLAIMED, "commit_sha": None, "base": None}
            if not self._record(claim_id, owner, restart):
                return self._lost()
            return self._open(app, req, claim_id, owner, first=False)
        if base is not None and head == commit:
            if not self._record(claim_id, owner, {"state": _PUSHED}):
                return self._lost()
            return self._pull(app, req, claim_id, owner, base, head)
        if not self._release(claim_id, owner):
            return self._lost()
        return self._skipped(app, req, base or req.base, head)

    # ------------------------------------------------------------------------- the writes

    def _open(
        self, app: GitHubApp, req: _Request, claim_id: str, owner: str, *, first: bool
    ) -> InvocationResult:
        """Steps 4 to 6 of the module doc under a claim this attempt holds (``first``: it
        inserted the claim, so no other attempt of this step was writing)."""
        try:
            base, prepared = self._prepare(app, req)
        except GitHubError:
            self._release(claim_id, owner)  # only unreferenced objects were made
            raise
        if isinstance(prepared, InvocationResult):
            released = self._release(claim_id, owner)
            if not released and prepared.outcome == COMPLETED:  # the branch may be theirs
                return self._lost()
            return prepared
        if not self._record(claim_id, owner, {"commit_sha": prepared, "base": base}):
            return self._lost()  # another attempt holds it now: make no branch
        try:
            app.create_branch(req.repo, req.branch, prepared)
        except GitHubError as exc:
            if exc.code == "http_422":  # the branch exists: someone else made it meanwhile
                # a taken-over claim keeps it: the branch may be an earlier attempt's
                if first and self._release(claim_id, owner):
                    return self._skipped(app, req, base, app.branch_head(req.repo, req.branch))
                return self._lost()
            if _refused(exc.code):
                self._release(claim_id, owner)
            else:  # the branch may exist: keep the claim, the next attempt reads first
                self._record(claim_id, owner, {"state": _UNKNOWN, "error": exc.code})
            raise
        if not self._record(claim_id, owner, {"state": _PUSHED}):
            if not self._follow_branch(claim_id, owner, req, base, prepared):
                return self._lost()
        return self._pull(app, req, claim_id, owner, base, prepared)

    def _prepare(self, app: GitHubApp, req: _Request) -> tuple[str, str | InvocationResult]:
        """``(base, commit)`` ready for the new branch, or ``(base, result)`` when it stops:
        a refusal or an existing branch."""
        default = app.get_repo(req.repo).get("default_branch")
        if not isinstance(default, str) or not default:
            raise GitHubError("bad_response", "default branch", retryable=True)
        base = req.base or default
        if req.branch.casefold() == default.casefold():
            log.info("github.pr_open refused: branch_is_default")
            return base, InvocationResult.failed("branch_is_default", retryable=False)
        if req.branch.casefold() == base.casefold():
            return base, InvocationResult.failed("branch_is_base", retryable=False)
        existing = app.branch_head(req.repo, req.branch)
        if existing is not None:
            return base, self._skipped(app, req, base, existing)
        base_sha = app.branch_head(req.repo, base)
        if base_sha is None:
            return base, InvocationResult.failed("base_not_found", retryable=False)
        tree = app.create_tree(req.repo, app.commit_tree(req.repo, base_sha), req.files)
        return base, app.create_commit(req.repo, req.title, tree, base_sha)

    def _pull(
        self, app: GitHubApp, req: _Request, claim_id: str, owner: str, base: str, head: str
    ) -> InvocationResult:
        """Open the pull for the pushed branch; any failure keeps the claim (``pushed``), so
        the next attempt reads before it opens again."""
        try:
            pull = app.open_pull(req.repo, req.title, req.branch, base, req.body)
        except GitHubError as exc:
            self._record(claim_id, owner, {"error": exc.code})
            raise
        out = self._opened(app, req, claim_id, owner, pull, head, base)
        return InvocationResult.completed(out)

    def _opened(
        self,
        app: GitHubApp,
        req: _Request,
        claim_id: str,
        owner: str,
        pull: Mapping[str, Any],
        head: str | None,
        base: str | None,
    ) -> dict[str, Any]:
        """Label the PR (an open one only), record it on the claim while this attempt owns
        it, and return the outputs."""
        labels_error = None
        if req.labels and pull.get("open", True):
            try:
                app.add_labels(req.repo, pull["number"], req.labels)
            except GitHubError as exc:  # the PR stands
                log.info("github.pr_open: labels not added (%s)", exc.code)
                labels_error = exc.code
        out = {
            "number": pull["number"],
            "url": pull.get("url"),
            "head_sha": head,
            "branch": req.branch,
            "base": base,
            "labels_error": labels_error,
        }
        self._record(claim_id, owner, {"state": _OPENED, **out})
        return out

    @staticmethod
    def _skipped(app: GitHubApp, req: _Request, base: str | None, head: str | None) -> Any:
        """Step 5: the branch exists, so nothing is opened."""
        pull = app.open_pull_for(req.repo, req.branch) or {}
        return InvocationResult.completed(
            {
                "number": pull.get("number"),
                "url": pull.get("url"),
                "head_sha": head,
                "branch": req.branch,
                "base": base,
                "labels_error": None,
                "skipped": "branch_exists",
            }
        )
