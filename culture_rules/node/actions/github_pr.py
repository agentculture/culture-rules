"""``github.push`` and ``github.review_reply`` action ports: PR work as the GitHub App.

Both reuse :class:`~culture_rules.node.actions.github.GitHubCommentPort`'s actor resolution
(a stored, enabled App actor with ``params.surface == "github"`` and a ``connection``), its
repo allowlist and its per-actor App cache. There is deliberately no merge port.

``github.push``
    Fast-forwards a same-repo PR's head branch to a local commit. Params: ``repo``
    (``owner/name``), ``number`` (the PR), ``head_branch``, ``expected_head_sha`` (the head
    the agent started from), ``source`` (an absolute path to a local git worktree, or to a
    self-contained git bundle file) and optional ``ref`` (the commit in ``source``; default
    ``HEAD``). In order, and refusing at the first failed check:

    1. actor, allowlist and input shape (no secret read, no network);
    2. the run's source rule (or, for a direct workflow run, its workflow) is still live and
       enabled;
    3. the commit is fetched into a fresh, node-owned bare repo (so nothing in the agent's
       repo config, hooks or credential helpers ever sees the token) and must descend from
       ``expected_head_sha``: a non-fast-forward update is refused before any network call;
    4. the PR (read as the App) must be open, its head and base repo both ``repo`` and its
       head ref ``head_branch``; its head SHA must equal ``expected_head_sha``;
    5. a fresh token is minted for this push alone: ``repositories=[repo]``,
       ``permissions={contents: write}``. It reaches git only through the child process's
       environment (an ``http.extraHeader``), never argv, a file or a log;
    6. ``git ls-remote`` with that token must still report ``expected_head_sha``;
    7. the rule is checked again, then one plain ``git push`` (no force, no ``+`` refspec,
       hooks off) of ``<sha>:refs/heads/<head_branch>``. The server also refuses non-ff.

    A retry after a lost acknowledgement finds the branch already at the commit and completes
    without pushing again, so the port is idempotent on its target state.

``github.review_reply``
    Replies in the review thread of ``comment_id`` on PR ``number`` (REST ``.../replies``) and,
    with ``resolve: true``, resolves the thread (GraphQL ``resolveReviewThread``), all as the
    App. The thread is located (or ``thread_id`` taken as given) *before* the reply is posted.

git always runs as an argv list with ``shell=False``; its stderr is discarded, never logged.
Standard-library only.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import shutil
import subprocess  # argv lists only, shell=False (B404/B603 skipped in pyproject)
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

from culture_rules.apps.github import DEFAULT_API_BASE, GitHubApp, GitHubError, Transport
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import (
    ADHOC_RULE_PREFIX,
    RULES_COLLECTION,
    RUNS_COLLECTION,
    WORKFLOWS_COLLECTION,
)
from culture_rules.node.actions.github import GitHubCommentPort

__all__ = [
    "DEFAULT_GIT_BASE",
    "GitHubPushPort",
    "GitHubReviewReplyPort",
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

#: ``runner(argv, env, timeout) -> (returncode, stdout)``; stderr is never surfaced.
GitRunner = Callable[[Sequence[str], Mapping[str, str], float], tuple[int, str]]


def subprocess_git(argv: Sequence[str], env: Mapping[str, str], timeout: float) -> tuple[int, str]:
    """Default runner: one ``git`` process, argv list, no shell, stdin closed."""
    try:
        proc = subprocess.run(
            list(argv),
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""
    return proc.returncode, proc.stdout or ""


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

    def __init__(self, runner: GitRunner, tmp: str) -> None:
        self._run = runner
        self._tmp = tmp
        self.repo = os.path.join(tmp, "push.git")

    def git(self, *args: str, token: str | None = None, in_repo: bool = True) -> tuple[int, str]:
        argv = ["git", "-c", "core.hooksPath=/dev/null", "-c", "credential.helper="]
        argv += ["-c", "protocol.ext.allow=never"]
        if in_repo:
            argv += ["-C", self.repo]
        return self._run([*argv, *args], _git_env(self._tmp, token), _GIT_TIMEOUT_S)

    def import_commit(self, source: str, ref: str) -> str:
        if self.git("init", "--bare", "--quiet", self.repo, in_repo=False)[0] != 0:
            raise _Refused("git_unavailable", retryable=True)
        rc, _ = self.git("fetch", "--no-tags", "--quiet", "--", source, f"{ref}:{_LOCAL_REF}")
        if rc != 0:
            raise _Refused("source_unreadable")
        rc, out = self.git("rev-parse", "--verify", "--quiet", f"{_LOCAL_REF}^{{commit}}")
        sha = out.strip()
        if rc != 0 or not _SHA_RE.match(sha):
            raise _Refused("source_unreadable")
        return sha

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
    ) -> None:
        super().__init__(store, transport=transport, secrets=secrets, api_base=api_base)
        self._git = git or subprocess_git
        self._git_base = git_base.rstrip("/")

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
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
        bad = _push_input_error(input)
        if bad:
            return InvocationResult.failed(bad, retryable=False)
        refusal = source_rule_refusal(self._store, context.run_id)
        if refusal:
            return InvocationResult.failed(refusal, retryable=False)
        tmp = tempfile.mkdtemp(prefix="culture-rules-push-")
        try:
            return self._push(
                str(actor_id), conn, allowed, input, context, _PushJob(self._git, tmp)
            )
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
        sha = job.import_commit(str(input["source"]), str(input.get("ref") or "HEAD"))
        out = {"repo": repo, "head_branch": branch, "head_before": expected, "head_after": sha}
        if sha == expected:
            return InvocationResult.completed({**out, "pushed": False})
        if not job.descends(expected, sha):
            raise _Refused("not_fast_forward")  # before any network call
        app = self._app(actor_id, conn, allowed)
        if app is None:
            raise _Refused("secret_unavailable")
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
        job.push(url, sha, branch, token)
        log.info("github.push: %s %s fast-forwarded", repo, branch)
        return InvocationResult.completed({**out, "pushed": True})


def _push_input_error(input: Mapping[str, Any]) -> str | None:
    """``bad_input`` unless every ``github.push`` param has a safe, expected shape."""
    try:
        number = int(input["number"])
    except (KeyError, TypeError, ValueError):
        return "bad_input"
    branch, expected = input.get("head_branch"), input.get("expected_head_sha")
    source, ref = input.get("source"), input.get("ref") or "HEAD"
    checks = (
        number > 0,
        isinstance(branch, str) and bool(_REF_RE.match(branch)) and ".." not in branch,
        isinstance(expected, str) and bool(_SHA_RE.match(expected)),
        isinstance(source, str) and os.path.isabs(source) and os.path.exists(source),
        isinstance(ref, str) and bool(_REF_RE.match(ref)) and ".." not in ref,
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
        _deadline: datetime,
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
        try:
            if resolve and not thread_id:
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
