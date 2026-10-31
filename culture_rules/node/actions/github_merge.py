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

1. actor (``actor_not_found``; :data:`NOT_APP`, ``actor_not_app``, for an actor whose
   ``kind`` is not ``app``: only an App's trust digest covers its connection), allowlist
   (``repo_not_allowed``, ``actor_misconfigured``) and input shape (``bad_input``);
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
   * the clicked button was posted by a trusted prompt (deviation d2,
     :data:`PROMPT_NOT_TRUSTED`, ``prompt_not_trusted``): the **stored** click event's
     ``data.posted_by`` (the gateway copies it from ``discord_buttons``, never from the
     click; never read from the run's inputs or params) names a ``run_id``, that run is in
     the store, and its pinned workflow is trusted in the ``merge_prompt`` role
     (:data:`~culture_rules.actors.trusted.ROLE_MERGE_PROMPT`, shipped empty like ``merge``).
     So an untrusted workflow cannot post a merge-prefixed button naming another PR;
   * its source rule is still live and enabled, read now (``rule_disabled``,
     :func:`~culture_rules.node.actions.github_pr.source_rule_refusal`);
   * the App actor's document (the one snapshot whose connection is used) matches a digest
     pinned in :data:`~culture_rules.actors.trusted.TRUSTED_ACTOR_DIGESTS`
     (``actor_not_trusted``, :func:`~culture_rules.actors.trusted.doc_refusal`), as
     ``github.push`` checks it: only a pinned App merges (``github-app`` is pinned);

3. the App (``secret_unavailable``; ``deadline_exceeded`` / ``lookup_busy`` retryable);
4. the installation token, then the once claim (below), then the PR read: it must be open
   (``pr_not_open``) with head ``sha`` (``head_moved``) - nothing is sent otherwise;
5. ``PUT /repos/{repo}/pulls/{number}/merge`` with ``{sha, merge_method: squash}``. GitHub
   refuses a head that moved (``http_409``) and a PR it cannot merge (``http_405``); every
   4xx but 408 fails non-retryable;
6. with ``delete_branch: true``, after a merged answer, and only when the PR's head repo is
   ``repo`` (never a fork's), ``DELETE /repos/{repo}/git/refs/heads/<head>``. A failed
   delete (a branch already gone answers 404/422) is not a failed merge: it is reported in
   ``branch_delete_error``. The result is recorded on the claim; a repeat that finds a
   merged claim with no recorded delete (``merged_before``, e.g. the first call did not ask
   for one or stopped before it) or reconciles a merge by a read (``merged_unconfirmed``)
   deletes the same way when ``delete_branch`` is true; a recorded delete (or delete
   error) is not sent again.

Outputs: ``merged`` (bool), ``sha`` (the merge commit GitHub made), ``branch_deleted``
(bool) and ``branch_delete_error`` (the delete's error code, or ``None``); a repeat adds
``skipped``.

The REST merge cannot take an idempotency key (``supports_idempotency_key`` is False), so
an unknown outcome is never sent again: the port claims ``sha256(repo#number#sha)`` by
insert in :data:`MERGE_ONCE_COLLECTION` before the PR read. Nothing sent (a refused read, a
refusal of step 4) or a merge GitHub refused with a 4xx but 408 releases it. Any other
failure (5xx, 408, a network error, the deadline, an unreadable answer) may follow a merge,
so the claim stays (state ``unknown``). A later invocation that finds the claim never sends
blindly: a ``merged`` claim completes with its outputs (``skipped: merged_before``), and a
``claimed``/``unknown`` one reads the PR - merged, it completes (``skipped:
merged_unconfirmed``). Not merged but still open at exactly the claimed sha, the merge did
not happen and sending it again is safe (GitHub merges a PR once, and the sha pins the
commit): an ``unknown`` claim, or a ``claimed`` one older than :data:`STALE_CLAIM_S` (a
crash mid-send), is taken back by compare-and-set and sent again, at most
:data:`MAX_RESENDS` times. Otherwise (a claim still in flight, the re-sends spent, or the
PR moved or closed) it fails ``merge_outcome_unknown``, non-retryable; a moved head or a
closed PR also releases the claim, since nothing was merged at that sha.
Standard-library only.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.actors import trusted
from culture_rules.apps.github import GitHubApp, GitHubError
from culture_rules.engine.actorport import InvocationContext, InvocationResult
from culture_rules.engine.runs import ADHOC_RULE_PREFIX, RUNS_COLLECTION, TERMINAL_STEPS
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
from culture_rules.node.actors import ACTORS_COLLECTION
from culture_rules.store.port import EVENTS_COLLECTION, DuplicateKeyError

__all__ = [
    "MERGE_ONCE_COLLECTION",
    "NOT_APP",
    "NOT_CLICK",
    "OUTCOME_UNKNOWN",
    "PROMPT_NOT_TRUSTED",
    "GitHubMergePort",
]

log = logging.getLogger(__name__)

MERGE_ONCE_COLLECTION = "github_merge_once"
"""Claims of ``github.merge`` calls (one document per repo, PR and sha)."""
NOT_CLICK = "not_started_by_click"
"""The run was not started by a ``discord.button.clicked`` event through its rule."""
PROMPT_NOT_TRUSTED = "prompt_not_trusted"
"""The clicked button was not posted by a run of a workflow trusted in ``merge_prompt``."""
NOT_APP = "actor_not_app"
"""The actor's ``kind`` is not ``app`` (its trust digest would not cover the connection)."""
OUTCOME_UNKNOWN = "merge_outcome_unknown"
"""An earlier merge of this repo, PR and sha may have happened; it is never sent again."""
_CLAIMED, _MERGED, _UNKNOWN = "claimed", "merged", "unknown"
MAX_RESENDS = 3
"""How many times a claim whose merge did not happen may be sent again."""
STALE_CLAIM_S = 600
"""A ``claimed`` claim older than this is a crash mid-send, not a merge in flight."""
_REFUSED = "github.merge refused: %s"


def _merge_input(input: Mapping[str, Any]) -> tuple[int, str, bool] | None:
    """``(number, sha, delete_branch)``, or ``None`` when any is malformed. ``number`` is an
    int only (the engine delivers the catalogue's ``int`` param as one, as ``github.pr`` reads
    it): a float, a bool or a string is refused, never truncated or parsed."""
    number, sha, delete = input.get("number"), input.get("sha"), input.get("delete_branch", False)
    if not isinstance(number, int) or isinstance(number, bool) or not isinstance(delete, bool):
        return None
    if number < 1 or not isinstance(sha, str) or len(sha) != 40:
        return None
    if any(c not in "0123456789abcdef" for c in sha):
        return None
    return number, sha, delete


def stored_click(store: Any, run: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The stored ``discord.button.clicked`` envelope ``run`` is its rule's firing on, or
    ``None`` when it is not such a run."""
    from culture_rules.node.firing import run_id_for  # noqa: PLC0415 - node layer, lazily

    trigger = run.get("trigger")
    rule_id = run.get("rule_id")
    if not isinstance(trigger, Mapping) or trigger.get("type") != BUTTON_CLICKED:
        return None
    event_id = trigger.get("id")
    if not isinstance(rule_id, str) or not rule_id or rule_id.startswith(ADHOC_RULE_PREFIX):
        return None
    if not isinstance(event_id, str) or not event_id:
        return None
    if run.get("id") != run_id_for(rule_id, event_id):
        return None  # started by hand (the API), not by the rule firing on the event
    stored = store.get(EVENTS_COLLECTION, event_id)
    envelope = (stored or {}).get("envelope")
    if not isinstance(envelope, Mapping) or envelope.get("type") != BUTTON_CLICKED:
        return None
    return envelope


def click_refusal(store: Any, run: Mapping[str, Any]) -> str | None:
    """:data:`NOT_CLICK` unless ``run`` is its rule's firing on a stored
    ``discord.button.clicked`` event; ``None`` when it is."""
    return None if stored_click(store, run) is not None else NOT_CLICK


def prompt_refusal(store: Any, envelope: Mapping[str, Any]) -> str | None:
    """:data:`PROMPT_NOT_TRUSTED` unless the stored click ``envelope``'s ``data.posted_by``
    names a stored run whose pinned workflow is trusted in ``merge_prompt``, and a step of that
    run that is one of the pinned workflow's own steps (never the rule's terminal action, which
    the workflow's digest does not cover); ``None`` when it does."""
    data = envelope.get("data")
    posted_by = data.get("posted_by") if isinstance(data, Mapping) else None
    run_id = posted_by.get("run_id") if isinstance(posted_by, Mapping) else None
    if not isinstance(run_id, str) or not run_id:
        return PROMPT_NOT_TRUSTED
    posting = store.get(RUNS_COLLECTION, run_id)
    if not isinstance(posting, Mapping):
        return PROMPT_NOT_TRUSTED
    if trusted.workflow_refusal(posting, (trusted.ROLE_MERGE_PROMPT,)):
        return PROMPT_NOT_TRUSTED
    if not _workflow_step(posting, posted_by.get("step")):
        return PROMPT_NOT_TRUSTED
    return None


def _workflow_step(run: Mapping[str, Any], key: Any) -> bool:
    """Whether ``key`` names a step state of ``run`` whose ``def`` is a step of the run's
    pinned workflow definition (loop bodies included), not a terminal action."""
    if not isinstance(key, str) or not key or key in TERMINAL_STEPS:
        return False
    states = [s for s in run.get("steps") or () if isinstance(s, Mapping) and s.get("key") == key]
    if len(states) != 1 or not isinstance(states[0].get("def"), str):
        return False
    definition = (run.get("workflow") or {}).get("definition") or {}
    return states[0]["def"] in _step_ids(definition.get("steps") or ())


def _step_ids(steps: Any) -> set[str]:
    ids: set[str] = set()
    for s in steps if isinstance(steps, list) else ():
        if isinstance(s, Mapping) and isinstance(s.get("id"), str):
            ids.add(s["id"])
            ids |= _step_ids(s.get("body"))
    return ids


def fence_refusal(store: Any, run_id: str | None) -> str | None:
    """Why the run ``run_id`` may not merge (module doc, step 2), or ``None``."""
    run = store.get(RUNS_COLLECTION, run_id) if run_id else None
    if not run:
        return "run_not_found"
    envelope = stored_click(store, run)
    if envelope is None:
        return NOT_CLICK
    refusal = trusted.workflow_refusal(run, (trusted.ROLE_MERGE,))
    if refusal:
        return refusal
    refusal = prompt_refusal(store, envelope)
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
        # one snapshot of the actor: its digest is checked and its connection is what is used
        snapshot = self._store.get(ACTORS_COLLECTION, actor_id) if actor_id else None
        conn = self._connection_of(snapshot)
        if conn is None:
            self._apps.pop(str(actor_id), None)
            return InvocationResult.failed(ACTOR_NOT_FOUND, retryable=False)
        if snapshot.get("kind") != "app":  # only an App's digest covers its connection
            log.info(_REFUSED, NOT_APP)
            return InvocationResult.failed(NOT_APP, retryable=False)
        repo = input.get("repo")
        allowed, refusal = repo_refusal(conn, repo)
        if refusal:
            return InvocationResult.failed(refusal, retryable=False)
        request = _merge_input(input)
        if request is None:
            return InvocationResult.failed(BAD_INPUT, retryable=False)
        refusal = fence_refusal(self._store, context.run_id)
        if not refusal:
            # as github.push: the App actor's security fields must match a digest pinned in code
            refusal, _digest = trusted.doc_refusal(actor_id, snapshot)
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
        claim = {
            "id": claim_id,
            "repo": repo,
            "number": number,
            "sha": sha,
            "state": _CLAIMED,
            "claimed_at": self._now().isoformat(),
            "resends": 0,
        }
        try:
            self._store.insert(MERGE_ONCE_COLLECTION, claim)
        except DuplicateKeyError:
            return self._prior(app, repo, number, sha, delete, claim_id)
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
        return self._send(app, repo, number, sha, delete, claim_id, pull)

    def _now(self) -> datetime:
        return self._clock() if self._clock is not None else datetime.now(UTC)

    def _send(
        self,
        app: GitHubApp,
        repo: str,
        number: int,
        sha: str,
        delete: bool,
        claim_id: str,
        pull: Mapping[str, Any],
    ) -> InvocationResult:
        """Send the merge for a claim this invocation holds (state ``claimed``)."""
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
            out.update(self._delete_recorded(app, repo, number, claim_id, pull))
        return InvocationResult.completed(out)

    def _delete_recorded(
        self,
        app: GitHubApp,
        repo: str,
        number: int,
        claim_id: str,
        pull: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Delete the merged PR's same-repo head (:func:`_delete_head`, reading the PR when
        ``pull`` is not given) and record the result on the ``merged`` claim, so a repeat
        neither skips a delete nor sends it twice. A failed read is reported, not recorded:
        a later repeat tries again."""
        if pull is None:
            try:
                pull = app.get_pull(repo, number)
            except GitHubError as exc:  # the merge stands; the delete waits for a repeat
                return {"branch_deleted": False, "branch_delete_error": exc.code}
        result = _delete_head(app, repo, pull)
        self._store.update_if(MERGE_ONCE_COLLECTION, claim_id, {"state": _MERGED}, result)
        return result

    def _settle_failure(self, claim_id: str, code: str) -> None:
        """A merge GitHub refused releases the claim; any other failure keeps it."""
        if _refused(code):
            self._store.delete(MERGE_ONCE_COLLECTION, claim_id)  # nothing was merged
            return
        changes = {"state": _UNKNOWN, "error": code}
        self._store.update_if(MERGE_ONCE_COLLECTION, claim_id, {"state": _CLAIMED}, changes)

    def _prior(
        self, app: GitHubApp, repo: str, number: int, sha: str, delete: bool, claim_id: str
    ) -> InvocationResult:
        """An earlier invocation claimed this merge: never send it blindly (module doc)."""
        prior = self._store.get(MERGE_ONCE_COLLECTION, claim_id) or {}
        if prior.get("state") == _MERGED:
            done = {
                "merged": True,
                "sha": prior.get("merge_sha"),
                "branch_deleted": prior.get("branch_deleted") is True,
                "branch_delete_error": prior.get("branch_delete_error"),
            }
            if delete and not done["branch_deleted"] and done["branch_delete_error"] is None:
                done.update(self._delete_recorded(app, repo, number, claim_id))
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
            if delete:
                out.update(self._delete_recorded(app, repo, number, claim_id, pull))
            return InvocationResult.completed({**out, "skipped": "merged_unconfirmed"})
        refusal = _pull_refusal(pull, sha)
        if refusal is not None:  # moved or closed unmerged: nothing was merged at this sha
            self._store.delete(MERGE_ONCE_COLLECTION, claim_id)
            log.info(_REFUSED, refusal)
            return InvocationResult.failed(refusal, retryable=False)
        if self._take_back(prior, claim_id):
            return self._send(app, repo, number, sha, delete, claim_id, pull)
        log.info(_REFUSED, OUTCOME_UNKNOWN)
        return InvocationResult.failed(OUTCOME_UNKNOWN, retryable=False)

    def _take_back(self, prior: Mapping[str, Any], claim_id: str) -> bool:
        """Whether this invocation may send again a claim whose merge did not happen: an
        ``unknown`` one, or a ``claimed`` one gone stale, with re-sends left, taken back by
        compare-and-set so two invocations never both send."""
        state, resends = prior.get("state"), prior.get("resends", 0)
        if not isinstance(resends, int) or resends >= MAX_RESENDS:
            return False
        if state == _CLAIMED:
            try:
                claimed_at = datetime.fromisoformat(str(prior.get("claimed_at")))
            except ValueError:
                return False
            if (self._now() - claimed_at).total_seconds() < STALE_CLAIM_S:
                return False
        elif state != _UNKNOWN:
            return False
        expected = {"state": state, "resends": resends}
        changes = {"state": _CLAIMED, "claimed_at": self._now().isoformat(), "resends": resends + 1}
        return self._store.update_if(MERGE_ONCE_COLLECTION, claim_id, expected, changes).won


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
