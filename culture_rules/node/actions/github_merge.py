"""``github.merge`` action port: squash-merge a PR as the GitHub App at the prompted sha.

The only merge in culture-rules, and merging stays a human gate: the port runs a merge only
in a run an operator's click started, in a workflow trusted to merge. It reuses
:class:`~culture_rules.node.actions.github.GitHubCommentPort`'s actor resolution (a stored,
enabled App actor with ``params.surface == "github"``), its repo allowlist, its per-actor App
cache and its deadline-bounded resolve (``_app_within``). Params: ``actor``, ``repo``,
``number``, ``sha`` (the full head commit the prompt was for) and optional ``delete_branch``
(bool). The merge method is always ``squash``.

In order, refusing at the first failed check; every check before step 4 reads only the
store, so a refusal reads no secret and makes no network call:

1. actor (``actor_not_found``), allowlist (``repo_not_allowed``, ``actor_misconfigured``) and
   input shape (``bad_input``);
2. the fence (``run_not_found`` when the run is gone):

   * the run was started by a ``discord.button.clicked`` event (:data:`NOT_CLICK`,
     ``not_started_by_click``, otherwise): its trigger is an event of that type, its id is
     its rule's firing on that event (:func:`~culture_rules.node.firing.run_id_for`), and
     the stored event (``events``) has that type too. A direct workflow run, a scheduled
     run, a run started through the API and a run chained from another rule are all
     refused. Which user may click is the click rule's ``users`` filter (configuration);
   * its **pinned** workflow is trusted in the ``merge`` role
     (:data:`~culture_rules.actors.trusted.ROLE_MERGE`, ``workflow_not_trusted``), as
     ``github.push``'s chain checks its roles. That role ships with no digest: a release
     pins the live merge workflow's;
   * its source rule is still live and enabled, read now (``rule_disabled``,
     :func:`~culture_rules.node.actions.github_pr.source_rule_refusal`);

3. the App (``secret_unavailable``; ``deadline_exceeded`` / ``lookup_busy`` retryable);
4. the installation token, then the once claim (below), then the PR read: it must be open
   (``pr_not_open``) with head ``sha`` (``head_moved``) - nothing is sent otherwise;
5. ``PUT /repos/{repo}/pulls/{number}/merge`` with ``{sha, merge_method: squash}``. GitHub
   refuses a head that moved (``http_409``) and a PR it cannot merge (``http_405``); every
   4xx but 408 fails non-retryable;
6. with ``delete_branch: true``, after a merged answer, and only when the PR's head repo is
   ``repo`` (never a fork's), ``DELETE /repos/{repo}/git/refs/heads/<head>``. A failed
   delete (a branch already gone answers 404/422) is not a failed merge: it is reported in
   ``branch_delete_error``.

Outputs: ``merged`` (bool), ``sha`` (the merge commit GitHub made), ``branch_deleted``
(bool) and ``branch_delete_error`` (the delete's error code, or ``None``); a repeat adds
``skipped``.

The REST merge cannot take an idempotency key (``supports_idempotency_key`` is False), so
an unknown outcome is never sent again: the port claims ``sha256(repo#number#sha)`` by
insert in :data:`MERGE_ONCE_COLLECTION` before the PR read. Nothing sent (a refused read, a
refusal of step 4) or a merge GitHub refused with a 4xx but 408 releases it. Any other
failure (5xx, 408, a network error, the deadline, an unreadable answer) may follow a merge,
so the claim stays (state ``unknown``). A later invocation that finds the claim sends
nothing: a ``merged`` claim completes with its outputs (``skipped: merged_before``), and a
``claimed``/``unknown`` one reads the PR - merged, it completes (``skipped:
merged_unconfirmed``); not merged, it fails ``merge_outcome_unknown``, non-retryable.
Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.actors import trusted
from culture_rules.apps.github import GitHubApp, GitHubError
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import ADHOC_RULE_PREFIX, RUNS_COLLECTION
from culture_rules.model.discord_filters import BUTTON_CLICKED
from culture_rules.node.actions.github import (
    ACTOR_NOT_FOUND,
    BAD_INPUT,
    SECRET_UNAVAILABLE,
    GitHubCommentPort,
    _refused,
    repo_refusal,
)
from culture_rules.node.actions.github_pr import source_rule_refusal
from culture_rules.store.port import EVENTS_COLLECTION, DuplicateKeyError

__all__ = ["MERGE_ONCE_COLLECTION", "NOT_CLICK", "OUTCOME_UNKNOWN", "GitHubMergePort"]

log = logging.getLogger(__name__)

MERGE_ONCE_COLLECTION = "github_merge_once"
"""Claims of ``github.merge`` calls (one document per repo, PR and sha)."""
NOT_CLICK = "not_started_by_click"
"""The run was not started by a ``discord.button.clicked`` event through its rule."""
OUTCOME_UNKNOWN = "merge_outcome_unknown"
"""An earlier merge of this repo, PR and sha may have happened; it is never sent again."""
_CLAIMED, _MERGED, _UNKNOWN = "claimed", "merged", "unknown"
_REFUSED = "github.merge refused: %s"


def _merge_input(input: Mapping[str, Any]) -> tuple[int, str, bool] | None:
    """``(number, sha, delete_branch)``, or ``None`` when any is malformed."""
    number, sha, delete = input.get("number"), input.get("sha"), input.get("delete_branch", False)
    if isinstance(number, bool) or not isinstance(delete, bool):
        return None
    try:
        number = int(number)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if number < 1 or not isinstance(sha, str) or len(sha) != 40:
        return None
    if any(c not in "0123456789abcdef" for c in sha):
        return None
    return number, sha, delete


def click_refusal(store: Any, run: Mapping[str, Any]) -> str | None:
    """:data:`NOT_CLICK` unless ``run`` is its rule's firing on a stored
    ``discord.button.clicked`` event; ``None`` when it is."""
    from culture_rules.node.firing import run_id_for  # noqa: PLC0415 - node layer, lazily

    trigger = run.get("trigger")
    rule_id = run.get("rule_id")
    if not isinstance(trigger, Mapping) or trigger.get("type") != BUTTON_CLICKED:
        return NOT_CLICK
    event_id = trigger.get("id")
    if not isinstance(rule_id, str) or not rule_id or rule_id.startswith(ADHOC_RULE_PREFIX):
        return NOT_CLICK
    if not isinstance(event_id, str) or not event_id:
        return NOT_CLICK
    if run.get("id") != run_id_for(rule_id, event_id):
        return NOT_CLICK  # started by hand (the API), not by the rule firing on the event
    stored = store.get(EVENTS_COLLECTION, event_id)
    envelope = (stored or {}).get("envelope")
    if not isinstance(envelope, Mapping) or envelope.get("type") != BUTTON_CLICKED:
        return NOT_CLICK
    return None


def fence_refusal(store: Any, run_id: str | None) -> str | None:
    """Why the run ``run_id`` may not merge (module doc, step 2), or ``None``."""
    run = store.get(RUNS_COLLECTION, run_id) if run_id else None
    if not run:
        return "run_not_found"
    refusal = click_refusal(store, run)
    if refusal:
        return refusal
    refusal = trusted.workflow_refusal(run, (trusted.ROLE_MERGE,))
    if refusal:
        return refusal
    return source_rule_refusal(store, str(run_id))


def _same(a: Any, b: str) -> bool:
    return isinstance(a, str) and a.lower() == b.lower()


class GitHubMergePort(GitHubCommentPort):
    """ActorPort for ``github.merge`` (see the module docstring)."""

    supports_idempotency_key = False

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
            return InvocationResult.failed(ACTOR_NOT_FOUND, retryable=False)
        repo = input.get("repo")
        allowed, refusal = repo_refusal(conn, repo)
        if refusal:
            return InvocationResult.failed(refusal, retryable=False)
        request = _merge_input(input)
        if request is None:
            return InvocationResult.failed(BAD_INPUT, retryable=False)
        refusal = fence_refusal(self._store, context.run_id)
        if refusal:
            log.info(_REFUSED, refusal)
            return InvocationResult.failed(refusal, retryable=False)
        try:
            app = self._app_within(str(actor_id), conn, allowed, deadline)
        except GitHubError as exc:
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        if app is None:
            return InvocationResult.failed(SECRET_UNAVAILABLE, retryable=False)
        try:
            with app.deadline(deadline):
                return self._merge_once(app, str(repo), *request)
        except GitHubError as exc:  # the token, before any claim
            return InvocationResult.failed(exc.code, retryable=exc.retryable)

    def _merge_once(
        self, app: GitHubApp, repo: str, number: int, sha: str, delete: bool
    ) -> InvocationResult:
        """The merge, at most once per (repo, PR, sha) (module doc)."""
        app.installation_token()  # before the claim: a token failure sends nothing
        claim_id = hashlib.sha256(f"{repo.lower()}#{number}#{sha}".encode()).hexdigest()
        claim = {"id": claim_id, "repo": repo, "number": number, "sha": sha, "state": _CLAIMED}
        try:
            self._store.insert(MERGE_ONCE_COLLECTION, claim)
        except DuplicateKeyError:
            return self._prior(app, repo, number, claim_id)
        try:
            pull = app.get_pull(repo, number)
        except GitHubError as exc:  # nothing was sent
            self._store.delete(MERGE_ONCE_COLLECTION, claim_id)
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        refusal = _pull_refusal(pull, sha)
        if refusal:
            self._store.delete(MERGE_ONCE_COLLECTION, claim_id)
            log.info(_REFUSED, refusal)
            return InvocationResult.failed(refusal, retryable=False)
        try:
            answer = app.merge_pull(repo, number, sha)
        except GitHubError as exc:
            self._settle_failure(claim_id, exc.code)
            return InvocationResult.failed(exc.code, retryable=exc.retryable)
        if not answer["merged"]:  # a 2xx that does not say merged: may still have merged
            self._settle_failure(claim_id, "bad_response")
            return InvocationResult.failed("bad_response", retryable=True)
        out: dict[str, Any] = {
            "merged": True,
            "sha": answer["sha"],
            "branch_deleted": False,
            "branch_delete_error": None,
        }
        merged = {"state": _MERGED, "merge_sha": answer["sha"]}  # ``sha`` stays the head's
        self._store.update_if(MERGE_ONCE_COLLECTION, claim_id, {"state": _CLAIMED}, merged)
        if delete:
            out.update(_delete_head(app, repo, pull))
            self._store.update_if(
                MERGE_ONCE_COLLECTION,
                claim_id,
                {"state": _MERGED},
                {k: out[k] for k in ("branch_deleted", "branch_delete_error")},
            )
        return InvocationResult.completed(out)

    def _settle_failure(self, claim_id: str, code: str) -> None:
        """A merge GitHub refused releases the claim; any other failure keeps it."""
        if _refused(code):
            self._store.delete(MERGE_ONCE_COLLECTION, claim_id)  # nothing was merged
            return
        changes = {"state": _UNKNOWN, "error": code}
        self._store.update_if(MERGE_ONCE_COLLECTION, claim_id, {"state": _CLAIMED}, changes)

    def _prior(self, app: GitHubApp, repo: str, number: int, claim_id: str) -> InvocationResult:
        """An earlier invocation claimed this merge: never send it again (module doc)."""
        prior = self._store.get(MERGE_ONCE_COLLECTION, claim_id) or {}
        if prior.get("state") == _MERGED:
            done = {
                "merged": True,
                "sha": prior.get("merge_sha"),
                "branch_deleted": prior.get("branch_deleted") is True,
                "branch_delete_error": prior.get("branch_delete_error"),
            }
            return InvocationResult.completed({**done, "skipped": "merged_before"})
        pull = app.get_pull(repo, number)  # a read: safe to retry on its own error
        commit = pull.get("merge_commit_sha")
        if pull.get("merged") is True and isinstance(commit, str):
            out = {"merged": True, "sha": commit, "branch_deleted": False}
            out["branch_delete_error"] = None
            self._store.update_if(
                MERGE_ONCE_COLLECTION,
                claim_id,
                {"state": prior.get("state")},
                {"state": _MERGED, "merge_sha": commit},
            )
            return InvocationResult.completed({**out, "skipped": "merged_unconfirmed"})
        log.info(_REFUSED, OUTCOME_UNKNOWN)
        return InvocationResult.failed(OUTCOME_UNKNOWN, retryable=False)


def _pull_refusal(pull: Mapping[str, Any], sha: str) -> str | None:
    """``pr_not_open`` or ``head_moved`` from the PR read, or ``None``."""
    if pull.get("state") != "open" or pull.get("merged") is True:
        return "pr_not_open"
    if (pull.get("head") or {}).get("sha") != sha:
        return "head_moved"
    return None


def _delete_head(app: GitHubApp, repo: str, pull: Mapping[str, Any]) -> dict[str, Any]:
    """Delete the merged PR's head branch when it lives in ``repo`` (never a fork's)."""
    head = pull.get("head") or {}
    base = pull.get("base") or {}
    same = _same((head.get("repo") or {}).get("full_name"), repo) and _same(
        (base.get("repo") or {}).get("full_name"), repo
    )
    if not same:
        return {"branch_deleted": False, "branch_delete_error": "not_same_repo_pr"}
    try:
        app.delete_ref(repo, head.get("ref"))
    except GitHubError as exc:  # e.g. already gone (repo auto-delete): the merge stands
        log.info("github.merge: head branch of %s not deleted (%s)", repo, exc.code)
        return {"branch_deleted": False, "branch_delete_error": exc.code}
    return {"branch_deleted": True, "branch_delete_error": None}
