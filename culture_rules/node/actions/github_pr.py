"""``github.push`` and ``github.review_reply`` action ports: PR work as the GitHub App.

Both reuse :class:`~culture_rules.node.actions.github.GitHubCommentPort`'s actor resolution
(a stored, enabled App actor with ``params.surface == "github"`` and a ``connection``), its
repo allowlist and its per-actor App cache. There is deliberately no merge port.

``github.push``
    Fast-forwards a same-repo PR's head branch to a local commit. Params: ``repo``
    (``owner/name``), ``number`` (the PR), ``head_branch``, ``expected_head_sha`` (the head
    the agent started from), ``commit_sha`` (the full SHA of the commit to push: immutable, so
    every retry targets the same commit whatever the worktree's HEAD is by then) and ``source``
    (an absolute path to a local git worktree, or to a git bundle, that contains
    ``commit_sha`` and its history back to ``expected_head_sha``; the test gate's ``bundle``
    output is one). Optional ``gate_verdict``: when the param is given at all it must be
    ``"pass"`` (:mod:`culture_rules.actors.gate`), else ``gate_not_passed`` before anything
    else is read, so a workflow that wires the gate's verdict in can never push a commit the
    gate did not pass. In order, refusing at the first failed check:

    1. actor, allowlist and input shape (no secret read, no network);
    2. the run's source rule (or, for a direct workflow run, its workflow) is still live and
       enabled;
    3. the run's review (d20, :func:`culture_rules.actors.review.review_refusal`): the
       review record of this run, written only by the built-in ``review`` step and never
       read from a param, must approve exactly ``commit_sha``, by a reviewer whose actor and
       backend both differ from the implementer's - else ``review_missing``,
       ``review_rejected``, ``review_commit_mismatch`` or ``reviewer_is_implementer``. This
       holds for every push, so a workflow that skips the review step pushes nothing.
       The approving record is re-read right before the final ``git push`` (step 8); a
       different current record refuses ``review_changed``. The PR's base (read in step 5)
       must still be the base the review recorded, else ``base_changed``;
    4. ``commit_sha`` is fetched into a fresh, node-owned bare repo (so nothing in the agent's
       repo config, hooks or credential helpers ever sees the token) and must descend from
       ``expected_head_sha``: a non-fast-forward update is refused before any network call.
       If the App actor sets ``params.commit_author`` (a git author name or email), every
       commit in ``expected_head_sha..commit_sha`` must carry it, else ``foreign_author``;
       unset, the check is off;
    5. the PR (read as the App) must be open, its head and base repo both ``repo`` and its
       head ref ``head_branch``; its head SHA must equal ``expected_head_sha``;
    6. a fresh token is minted for this push alone: ``repositories=[repo]``,
       ``permissions={contents: write}``. It reaches git only through the child process's
       environment (an ``http.extraHeader``), never argv, a file or a log;
    7. ``git ls-remote`` with that token must still report ``expected_head_sha``;
    8. the rule is checked again, then one plain ``git push`` (no force, no ``+`` refspec,
       hooks off) of ``<sha>:refs/heads/<head_branch>``. The server also refuses non-ff.

    Every git and HTTP call is bounded by the time left before the invocation's deadline
    (past it the executor stops renewing the step's claim and another host may take over),
    and the push itself is not started with under ``PUSH_MARGIN_S`` (10 s) left: both fail
    ``deadline_exceeded``, retryable, with nothing pushed.

    A retry after a lost acknowledgement finds the branch already at the commit and completes
    without pushing again, so the port is idempotent on its target state.

``github.review_reply``
    Replies in the review thread of ``comment_id`` on PR ``number`` (REST ``.../replies``) and,
    with ``resolve: true``, resolves the thread (GraphQL ``resolveReviewThread``), all as the
    App. Before anything is posted the thread is located, or a supplied ``thread_id`` is
    verified to belong to ``repo``, PR ``number`` and ``comment_id`` (``thread_mismatch``
    otherwise): the installation token could otherwise resolve any thread it can reach.

``github.threads`` (a built-in code step, d15)
    Lists PR ``number``'s unresolved review threads as the App (read-only) and keeps those
    whose opening comment's author is in ``trusted_authors`` (case-insensitive): output
    ``threads`` (``{thread_id, comment_id, path, line, author, body}`` each) and the count of
    ``untrusted`` ones left out. The App actor is ``config.actor``, else the step's placement
    actor. Fail closed: a lookup error, the page cap or bad input fails the step, so no
    thread list is ever handed on.

``github.threads_addressed`` (a built-in code step, d15)
    Pure: of the agent's ``addressed`` entries (``{thread_id, commit, reply}``), keeps each
    whose ``thread_id`` is in ``threads`` (once) and outputs ``replies``: ``{thread_id,
    comment_id, commit, reply}``. With a ``commit`` input (the pushed SHA), every reply names
    that commit instead of the agent's. An id that is not in the list is ``dropped``, never
    answered.

git always runs as an argv list with ``shell=False``; its stderr is discarded, never logged.
Standard-library only.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import signal
import subprocess  # argv lists only, shell=False (B404/B603 skipped in pyproject)
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors.review import (
    REVIEWS_COLLECTION,
    approved_review,
    consume_approval,
)
from culture_rules.actors.trusted import actor_refusal, workflow_refusal
from culture_rules.apps.github import DEFAULT_API_BASE, GitHubApp, GitHubError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import (
    ADHOC_RULE_PREFIX,
    RULES_COLLECTION,
    RUNS_COLLECTION,
    WORKFLOWS_COLLECTION,
)
from culture_rules.node.actions.github import GitHubCommentPort
from culture_rules.node.actors import ACTORS_COLLECTION

__all__ = [
    "ADDRESSED_BUILTIN",
    "AddressedThreadsPort",
    "DEFAULT_GIT_BASE",
    "THREADS_BUILTIN",
    "GitHubThreadsPort",
    "GitHubPushPort",
    "GitHubReviewReplyPort",
    "GIT_TIMED_OUT",
    "GIT_UNAVAILABLE",
    "GitRunner",
    "source_rule_refusal",
    "subprocess_git",
]

log = logging.getLogger(__name__)

DEFAULT_GIT_BASE = "https://github.com"
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
_LOCAL_REF = "refs/culture-rules/push"

_GIT_TIMEOUT_S = 120.0
_TERM_GRACE_S = 2.0
#: Runner return codes outside git's own range (git exits 0..255; signals are negative).
GIT_TIMED_OUT = -1000
GIT_UNAVAILABLE = -1001
#: The network push is not started with less than this left before the step's deadline.
PUSH_MARGIN_S = 10.0

#: ``runner(argv, env, timeout) -> (returncode, stdout)``; stderr is never surfaced. A
#: runner returns :data:`GIT_TIMED_OUT` / :data:`GIT_UNAVAILABLE` for a timeout / no git.
GitRunner = Callable[[Sequence[str], Mapping[str, str], float], tuple[int, str]]


def _signal_group(pgid: int, sig: int) -> bool:
    """Send ``sig`` to process group ``pgid``; False once the group no longer exists."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - only our own children are in the group
        return False
    return True


def _wait_group_gone(pgid: int, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not _signal_group(pgid, 0):
            return True
        time.sleep(0.05)
    return not _signal_group(pgid, 0)


def _kill_group(proc: subprocess.Popen[str]) -> None:
    """SIGTERM then SIGKILL git's whole process group (git-remote-https, send-pack, ...),
    and reap git, so nothing of this push outlives the call."""
    pgid = proc.pid  # start_new_session: git leads its own group
    _signal_group(pgid, signal.SIGTERM)
    if not _wait_group_gone(pgid, _TERM_GRACE_S):
        _signal_group(pgid, signal.SIGKILL)
        _wait_group_gone(pgid, _TERM_GRACE_S)
    try:
        proc.communicate(timeout=_TERM_GRACE_S)  # reap git, drain and close the pipe
    except subprocess.TimeoutExpired:  # pragma: no cover - the group is already gone
        proc.kill()
        proc.wait()


def subprocess_git(argv: Sequence[str], env: Mapping[str, str], timeout: float) -> tuple[int, str]:
    """Default runner: one ``git`` process group, argv list, no shell, stdin closed.

    Returns :data:`GIT_TIMED_OUT` when ``timeout`` passes (the whole group is killed and
    reaped first) and :data:`GIT_UNAVAILABLE` when git cannot be started."""
    try:
        proc = subprocess.Popen(
            list(argv),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            shell=False,
            start_new_session=True,
        )
    except OSError:
        return GIT_UNAVAILABLE, ""
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(proc)
        return GIT_TIMED_OUT, ""
    # git exited; a helper it left behind in its group must not keep running either
    if _signal_group(proc.pid, 0):
        _signal_group(proc.pid, signal.SIGKILL)
    return proc.returncode, out or ""


def _live(doc: Mapping[str, Any] | None) -> bool:
    return bool(doc) and not doc.get("deleted_at") and doc.get("enabled") is not False


def source_rule_refusal(store: Any, run_id: str) -> str | None:
    """Why the run ``run_id`` may no longer act (``rule_disabled`` ...), or ``None`` if it may.

    The rule is read from the store *now*, not from the run's pinned copy, so disabling a
    rule mid-run stops its pushes. A direct workflow run (``adhoc:`` rule) checks its workflow.
    """
    run = store.get(RUNS_COLLECTION, run_id) if run_id else None
    if not run:
        return "run_not_found"
    rule_id = run.get("rule_id")
    if isinstance(rule_id, str) and rule_id.startswith(ADHOC_RULE_PREFIX):
        workflow_id = run.get("workflow_id")
        doc = store.get(WORKFLOWS_COLLECTION, workflow_id) if workflow_id else None
        return None if _live(doc) else "workflow_disabled"
    doc = store.get(RULES_COLLECTION, rule_id) if isinstance(rule_id, str) else None
    return None if _live(doc) else "rule_disabled"


class _Refused(Exception):
    """A push check failed: ``code`` becomes the step error."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _git_env(home: str, token: str | None = None) -> dict[str, str]:
    """A minimal, config-free environment; the token rides only as an http.extraHeader."""
    env = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": home,
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    if token is not None:
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode("ascii")
        env.update(
            GIT_CONFIG_COUNT="1",
            GIT_CONFIG_KEY_0="http.extraHeader",
            GIT_CONFIG_VALUE_0=f"Authorization: Basic {basic}",
        )
    return env


def _same(a: Any, b: str) -> bool:
    return isinstance(a, str) and a.lower() == b.lower()


class _PushJob:
    """One ``github.push`` invocation's local repo and git calls (see the module docstring)."""

    def __init__(
        self, runner: GitRunner, tmp: str, deadline: datetime, clock: Callable[[], datetime]
    ) -> None:
        self._run = runner
        self._tmp = tmp
        self.repo = os.path.join(tmp, "push.git")
        self.deadline = deadline
        self.clock = clock
        self.review_record: str | None = None

    def require(self, margin: float = 0.0) -> float:
        """Seconds left before the deadline; ``deadline_exceeded`` if not more than ``margin``.

        Past the deadline the executor stops renewing the step's claim and another host may
        take the step over, so nothing may still be running (or start pushing) by then."""
        left = (self.deadline - self.clock()).total_seconds()
        if left <= margin:
            raise _Refused("deadline_exceeded", retryable=True)
        return left

    def git(self, *args: str, token: str | None = None, in_repo: bool = True) -> tuple[int, str]:
        timeout = min(_GIT_TIMEOUT_S, self.require())
        argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]
        argv += ["-c", "protocol.ext.allow=never"]
        if in_repo:
            argv += ["-C", self.repo]
        rc, out = self._run([*argv, *args], _git_env(self._tmp, token), timeout)
        if rc == GIT_TIMED_OUT:  # never a validation verdict: say so, and let it retry
            self.require()  # deadline_exceeded if the deadline has passed meanwhile
            raise _Refused("git_timeout", retryable=True)
        if rc == GIT_UNAVAILABLE:
            raise _Refused("git_unavailable", retryable=True)
        return rc, out

    def import_commit(self, source: str, sha: str) -> None:
        """Fetch exactly ``sha`` (and its history) from ``source``; no mutable ref is read."""
        if self.git("init", "--bare", "--quiet", self.repo, in_repo=False)[0] != 0:
            raise _Refused("git_unavailable", retryable=True)
        rc, _ = self.git("fetch", "--no-tags", "--quiet", "--", source, f"{sha}:{_LOCAL_REF}")
        if rc != 0:
            raise _Refused("commit_not_found")
        rc, out = self.git("rev-parse", "--verify", "--quiet", f"{_LOCAL_REF}^{{commit}}")
        if rc != 0 or out.strip() != sha:
            raise _Refused("commit_not_found")

    def authors(self, base: str, sha: str) -> list[tuple[str, str]] | None:
        """``(name, email)`` of every commit in ``base..sha``, or ``None`` if git failed."""
        rc, out = self.git("log", "--format=%an%x00%ae", f"{base}..{sha}", "--")
        if rc != 0:
            return None
        return [tuple(line.split("\x00", 1)) for line in out.splitlines() if "\x00" in line]

    def descends(self, base: str, sha: str) -> bool:
        if self.git("cat-file", "-e", f"{base}^{{commit}}")[0] != 0:
            return False
        return self.git("merge-base", "--is-ancestor", base, sha)[0] == 0

    def remote_head(self, url: str, branch: str, token: str) -> str | None:
        rc, out = self.git("ls-remote", "--heads", "--", url, f"refs/heads/{branch}", token=token)
        if rc != 0:
            raise _Refused("remote_unreachable", retryable=True)
        for line in out.splitlines():
            sha, _, name = line.partition("\t")
            if name.strip() == f"refs/heads/{branch}":
                return sha.strip()
        return None

    def push(self, url: str, sha: str, branch: str, token: str) -> None:
        # a plain, non-force push: no --force / --force-with-lease / + refspec, hooks off
        rc, out = self.git(
            "push",
            "--porcelain",
            "--no-verify",
            "--",
            url,
            f"{sha}:refs/heads/{branch}",
            token=token,
        )
        if rc != 0:
            rejected = "[rejected]" in out or "[remote rejected]" in out
            raise _Refused("push_rejected" if rejected else "push_failed", retryable=not rejected)


class GitHubPushPort(GitHubCommentPort):
    """ActorPort for ``github.push`` (see the module docstring)."""

    supports_idempotency_key = True  # a retry finds the branch at the commit and stops

    def __init__(
        self,
        store: Any,
        *,
        transport: Transport | None = None,
        secrets: Callable[[str], str] | None = None,
        api_base: str = DEFAULT_API_BASE,
        git: GitRunner | None = None,
        git_base: str = DEFAULT_GIT_BASE,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        super().__init__(store, transport=transport, secrets=secrets, api_base=api_base)
        self._git = git or subprocess_git
        self._git_base = git_base.rstrip("/")
        self._clock = clock or (lambda: datetime.now(UTC))

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        if "gate_verdict" in input and input["gate_verdict"] != "pass":
            return InvocationResult.failed("gate_not_passed", retryable=False)
        actor_id = context.actor or input.get("actor")
        conn = self._connection(actor_id)
        if conn is None:
            self._apps.pop(str(actor_id), None)
            return InvocationResult.failed("actor_not_found", retryable=False)
        repo = input.get("repo")
        allowed = {str(r).lower() for r in conn.get("repos") or ()}
        if not GitHubApp.is_repo_name(repo) or repo.lower() not in allowed:
            return InvocationResult.failed("repo_not_allowed", retryable=False)
        if not conn.get("app_id") or not conn.get("installation_id"):
            return InvocationResult.failed("actor_misconfigured", retryable=False)
        bad = _push_input_error(input)
        if bad:
            return InvocationResult.failed(bad, retryable=False)
        refusal = source_rule_refusal(self._store, context.run_id)
        if refusal:
            return InvocationResult.failed(refusal, retryable=False)
        # d20 round 2: only a run of a workflow pinned as trusted in code may push
        refusal = workflow_refusal(self._store.get(RUNS_COLLECTION, context.run_id))
        if refusal:
            log.info("github.push refused: %s", refusal)
            return InvocationResult.failed(refusal, retryable=False)
        # d20: the run's reviewer must have approved exactly this commit (read from the
        # store, never a param), whatever the workflow wires
        refusal, review_record = self._review(input, context)
        if refusal:
            log.info("github.push refused: %s", refusal)
            return InvocationResult.failed(refusal, retryable=False)
        # round 3 (#1): the App actor's security fields must match a digest pinned in code
        refusal, _digest = actor_refusal(self._store, actor_id)
        if refusal:
            log.info("github.push refused: %s", refusal)
            return InvocationResult.failed(refusal, retryable=False)
        if self._clock() >= deadline:
            return InvocationResult.failed("deadline_exceeded", retryable=True)
        tmp = tempfile.mkdtemp(prefix="culture-rules-push-")
        job = _PushJob(self._git, tmp, deadline, self._clock)
        job.review_record = review_record
        try:
            return self._push(str(actor_id), conn, allowed, input, context, job)
        except _Refused as exc:
            log.info("github.push refused: %s", exc.code)
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _push(
        self,
        actor_id: str,
        conn: Mapping[str, Any],
        allowed: set[str],
        input: Mapping[str, Any],
        context: InvocationContext,
        job: _PushJob,
    ) -> InvocationResult:
        repo, branch = str(input["repo"]), str(input["head_branch"])
        expected = str(input["expected_head_sha"])
        if job.git("check-ref-format", f"refs/heads/{branch}", in_repo=False)[0] != 0:
            raise _Refused("bad_input")
        sha = str(input["commit_sha"])
        out = {"repo": repo, "head_branch": branch, "head_before": expected, "head_after": sha}
        if sha == expected:
            return InvocationResult.completed({**out, "pushed": False})
        job.import_commit(str(input["source"]), sha)
        if not job.descends(expected, sha):
            raise _Refused("not_fast_forward")  # before any network call
        self._check_authors(actor_id, job, expected, sha)
        app = self._app(actor_id, conn, allowed)
        if app is None:
            raise _Refused("secret_unavailable")
        with app.deadline(job.deadline, job.clock):  # every HTTP call bounded too
            return self._publish(app, input, context, job, out)

    def _publish(
        self,
        app: GitHubApp,
        input: Mapping[str, Any],
        context: InvocationContext,
        job: _PushJob,
        out: dict[str, Any],
    ) -> InvocationResult:
        repo, branch, expected, sha = (
            out["repo"],
            out["head_branch"],
            out["head_before"],
            out["head_after"],
        )
        pull = app.get_pull(repo, int(input["number"]))
        head, base = pull.get("head") or {}, pull.get("base") or {}
        if pull.get("state") != "open":
            raise _Refused("pr_not_open")
        if not _same((head.get("repo") or {}).get("full_name"), repo) or not _same(
            (base.get("repo") or {}).get("full_name"), repo
        ):
            raise _Refused("not_same_repo_pr")
        if head.get("ref") != branch:
            raise _Refused("not_pr_head_branch")
        if head.get("sha") == sha:
            return InvocationResult.completed({**out, "pushed": False, "already": True})
        if head.get("sha") != expected:
            raise _Refused("head_moved")
        # round 4 (#2): the gate policy came from the base the review recorded; a base that
        # moved or was retargeted since then means it is not the policy this commit passed.
        # The next checks event runs the gate and the review again against the new base.
        reviewed = self._store.get(REVIEWS_COLLECTION, job.review_record or "") or {}
        if not isinstance(base.get("sha"), str) or base.get("sha") != reviewed.get("base_sha"):
            raise _Refused("base_changed")
        url = f"{self._git_base}/{repo}.git"
        token = app.push_token(repo)  # held by this call alone; never cached or logged
        remote = job.remote_head(url, branch, token)
        if remote == sha:
            return InvocationResult.completed({**out, "pushed": False, "already": True})
        if remote != expected:
            raise _Refused("head_moved")
        refusal = source_rule_refusal(self._store, context.run_id)
        if refusal:
            raise _Refused(refusal)
        # d20 (Codex review #6): the approval is re-read right before the push; a newer
        # review result, or any other current record, stops it
        refusal, record = self._review(input, context)
        if refusal:
            raise _Refused(refusal)
        if record != job.review_record:
            raise _Refused("review_changed")
        # round 2 (#4): consume the approval - a compare-and-set no later verdict can undo
        refusal = consume_approval(
            self._store, context.run_id, record, sha, by=f"{context.step_id}#{context.attempt}"
        )
        if refusal:
            raise _Refused(refusal)
        job.require(PUSH_MARGIN_S)  # never start the push this close to the deadline
        job.push(url, sha, branch, token)
        log.info("github.push: %s %s fast-forwarded", repo, branch)
        return InvocationResult.completed({**out, "pushed": True})

    def _review(
        self, input: Mapping[str, Any], context: InvocationContext
    ) -> tuple[str | None, str | None]:
        """This run's current review record, judged for exactly this push (d20)."""
        return approved_review(
            self._store,
            context.run_id,
            str(input["commit_sha"]),
            repo=input.get("repo"),
            number=input.get("number"),
            start_sha=input.get("expected_head_sha"),
        )

    def _check_authors(self, actor_id: str, job: _PushJob, base: str, sha: str) -> None:
        """``foreign_author`` unless every new commit is by the actor's ``commit_author``."""
        doc = self._store.get(ACTORS_COLLECTION, actor_id) or {}
        want = (doc.get("params") or {}).get("commit_author")
        if not want:
            return  # off unless configured
        want = str(want).strip().lower()
        authors = job.authors(base, sha)
        if not authors or any(want not in (n.lower(), e.lower()) for n, e in authors):
            raise _Refused("foreign_author")


def _push_input_error(input: Mapping[str, Any]) -> str | None:
    """``bad_input`` unless every ``github.push`` param has a safe, expected shape."""
    try:
        number = int(input["number"])
    except (KeyError, TypeError, ValueError):
        return "bad_input"
    branch, expected = input.get("head_branch"), input.get("expected_head_sha")
    source, sha = input.get("source"), input.get("commit_sha")
    checks = (
        number > 0,
        isinstance(branch, str) and bool(_REF_RE.match(branch)) and ".." not in branch,
        isinstance(expected, str) and bool(_SHA_RE.match(expected)),
        isinstance(source, str) and os.path.isabs(source) and os.path.exists(source),
        isinstance(sha, str) and bool(_SHA_RE.match(sha)),
    )
    return None if all(checks) else "bad_input"


def _want_resolve(value: Any) -> bool | None:
    """``resolve`` as a bool (``"true"``/``"false"`` from a template accepted), else ``None``."""
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    return None


class GitHubReviewReplyPort(GitHubCommentPort):
    """ActorPort for ``github.review_reply`` (see the module docstring)."""

    supports_idempotency_key = False  # GitHub cannot deduplicate replies

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        actor_id = context.actor or input.get("actor")
        conn = self._connection(actor_id)
        if conn is None:
            self._apps.pop(str(actor_id), None)
            return InvocationResult.failed("actor_not_found", retryable=False)
        repo = input.get("repo")
        allowed = {str(r).lower() for r in conn.get("repos") or ()}
        if not GitHubApp.is_repo_name(repo) or repo.lower() not in allowed:
            return InvocationResult.failed("repo_not_allowed", retryable=False)
        if not conn.get("app_id") or not conn.get("installation_id"):
            return InvocationResult.failed("actor_misconfigured", retryable=False)
        try:
            number, comment_id = int(input["number"]), int(input["comment_id"])
            body = str(input["body"])
        except (KeyError, TypeError, ValueError):
            return InvocationResult.failed("bad_input", retryable=False)
        resolve, thread_id = _want_resolve(input.get("resolve")), input.get("thread_id")
        if resolve is None or (thread_id is not None and not isinstance(thread_id, str)):
            return InvocationResult.failed("bad_input", retryable=False)
        app = self._app(str(actor_id), conn, allowed)
        if app is None:
            return InvocationResult.failed("secret_unavailable", retryable=False)
        with app.deadline(deadline):  # every HTTP call bounded by the step's deadline
            return self._reply(app, repo, number, comment_id, body, resolve, thread_id)

    @staticmethod
    def _reply(
        app: GitHubApp,
        repo: str,
        number: int,
        comment_id: int,
        body: str,
        resolve: bool,
        thread_id: str | None,
    ) -> InvocationResult:
        try:
            if thread_id:  # never trust a supplied id: it must be this PR's comment thread
                if not app.review_thread_matches(repo, number, thread_id, comment_id):
                    return InvocationResult.failed("thread_mismatch", retryable=False)
            elif resolve:
                thread_id = app.find_review_thread(repo, number, comment_id)
                if not thread_id:
                    return InvocationResult.failed("thread_not_found", retryable=False)
            out = app.reply_review_comment(repo, number, comment_id, body)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        resolved = False
        if resolve:
            try:
                resolved = app.resolve_review_thread(str(thread_id))
            except GitHubError as exc:  # the reply exists: a retry would post it twice
                return InvocationResult.failed(f"resolve_failed: {exc.code}", retryable=False)
            if not resolved:
                return InvocationResult.failed("resolve_failed", retryable=False)
        return InvocationResult.completed({**out, "thread_id": thread_id, "resolved": resolved})


THREADS_BUILTIN = "github.threads"
"""``config.builtin`` of the code step that lists trusted, unresolved review threads."""
ADDRESSED_BUILTIN = "github.threads_addressed"
"""``config.builtin`` of the code step that matches the agent's report against that list."""


class GitHubThreadsPort(GitHubCommentPort):
    """Built-in ``github.threads`` (see the module docstring)."""

    supports_idempotency_key = True  # a read: re-asking is harmless

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        actor_id = (context.config or {}).get("actor") or context.actor
        conn = self._connection(actor_id)
        if conn is None:
            self._apps.pop(str(actor_id), None)
            return InvocationResult.failed("actor_not_found", retryable=False)
        repo = input.get("repo")
        allowed = {str(r).lower() for r in conn.get("repos") or ()}
        if not GitHubApp.is_repo_name(repo) or repo.lower() not in allowed:
            return InvocationResult.failed("repo_not_allowed", retryable=False)
        trusted = input.get("trusted_authors")
        number = input.get("number")
        if (
            not isinstance(trusted, list)
            or not all(isinstance(a, str) for a in trusted)
            or not isinstance(number, int)
            or isinstance(number, bool)
        ):
            return InvocationResult.failed("bad_input", retryable=False)
        if not conn.get("app_id") or not conn.get("installation_id"):
            return InvocationResult.failed("actor_misconfigured", retryable=False)
        app = self._app(str(actor_id), conn, allowed)
        if app is None:
            return InvocationResult.failed("secret_unavailable", retryable=False)
        try:
            with app.deadline(deadline):
                listed = app.list_review_threads(repo, number)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        names = {a.casefold() for a in trusted}
        kept = [t for t in listed if str(t.get("author") or "").casefold() in names]
        return InvocationResult.completed({"threads": kept, "untrusted": len(listed) - len(kept)})


class AddressedThreadsPort:
    """Built-in ``github.threads_addressed`` (see the module docstring). No I/O."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        del context
        threads, addressed = input.get("threads"), input.get("addressed")
        if not isinstance(threads, list) or not isinstance(addressed, list):
            return InvocationResult.failed("bad_input", retryable=False)
        listed = {
            t["thread_id"]: t["comment_id"]
            for t in threads
            if isinstance(t, Mapping)
            and isinstance(t.get("thread_id"), str)
            and isinstance(t.get("comment_id"), int)
        }
        pushed_commit = input.get("commit")
        if pushed_commit is not None and not (
            isinstance(pushed_commit, str) and _SHA_RE.match(pushed_commit)
        ):
            return InvocationResult.failed("bad_input", retryable=False)
        replies: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in addressed:
            tid = entry.get("thread_id") if isinstance(entry, Mapping) else None
            if not isinstance(tid, str) or tid not in listed or tid in seen:
                continue
            seen.add(tid)
            reply = entry.get("reply")
            replies.append(
                {
                    "thread_id": tid,
                    "comment_id": listed[tid],
                    # the pushed (gate-built) commit when given: the agent's own
                    # commits never reach GitHub, so its SHAs would name nothing
                    "commit": pushed_commit or entry.get("commit"),
                    "reply": reply if isinstance(reply, str) else "",
                }
            )
        return InvocationResult.completed(
            {"replies": replies, "dropped": len(addressed) - len(replies)}
        )
