"""The ``github`` extension: GitHub as a GitHub App (the ``github`` extra, ``cryptography``).

* action kinds ``github.comment`` and ``github.pr`` and the head lookup ``github.pr_head``
  (:mod:`~culture_rules.extensions.github.port`), and ``github.merge``
  (:mod:`~culture_rules.extensions.github.merge`, fenced by an operator's click carrying the
  button's one-time code from the ``discord`` extension), and ``github.pr_open``
  (:mod:`~culture_rules.extensions.github.pr_open`: files committed to a new branch and a PR
  opened, never on the default branch), over the App client
  :mod:`~culture_rules.extensions.github.client`;
* ``POST /hooks/github`` (:mod:`~culture_rules.extensions.github.hooks`) and the checks
  settler (:mod:`~culture_rules.extensions.github.checks_settle`, a node listener and the
  webhook's settle-on-check), their event types and collections;
* the event types and id prefixes only the engine writes, reserved at external ingest
  (:mod:`~culture_rules.extensions.github.events`);
* :mod:`~culture_rules.extensions.github.public_text`, untrusted text made inert for a public
  comment.

Registering imports nothing but the kinds' typed params; without ``cryptography`` the ports
are ``extra_missing``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from culture_rules.extensions.github import events
from culture_rules.extensions.registry import Extension, Lazy, NodeContext, Problem
from culture_rules.model.action_kinds import (
    ADV,
    REQ_INT,
    REQ_STR,
    REQ_TEXT,
    TEXT,
    ParamSpec,
    kind,
)
from culture_rules.model.refs import is_input_reference, is_reference
from culture_rules.model.serde import join

__all__ = ["EVENT_TYPES", "KINDS", "register"]

GITHUB_COMMENT = "github.comment"
GITHUB_MERGE = "github.merge"
GITHUB_PR = "github.pr"
GITHUB_PR_OPEN = "github.pr_open"

KINDS = (
    kind(
        GITHUB_COMMENT,
        "Comment on a GitHub issue or PR",
        label="Comment on GitHub",
        declarable_by_apps=True,
        actor=REQ_STR,
        repo=REQ_STR,
        number=REQ_INT,
        body=REQ_TEXT,
        once_key=ADV,  # d25: post at most once per (repo, PR, key), durably
    ),
    kind(
        GITHUB_MERGE,
        "Merge a PR as the GitHub App, only from a click carrying the button's one-time code",
        label="Merge a GitHub PR",
        declarable_by_apps=True,
        actor=REQ_STR,
        repo=REQ_STR,
        number=REQ_INT,
        sha=REQ_STR,
        delete_branch=ParamSpec("bool"),
    ),
    kind(
        GITHUB_PR,
        "Read one PR's title, link, author, draft flag, state, repos, head branch and sha"
        " as the GitHub App",
        label="Read a GitHub PR",
        declarable_by_apps=True,
        actor=REQ_STR,
        repo=REQ_STR,
        number=REQ_INT,
    ),
    kind(
        GITHUB_PR_OPEN,
        "Commit files to a new branch and open a PR as the GitHub App, never on the default"
        " branch",
        label="Open a GitHub PR",
        declarable_by_apps=True,
        actor=REQ_STR,
        repo=REQ_STR,
        branch=ParamSpec("str", True, label="New branch"),
        base=ParamSpec("str", label="Base branch (empty: the default branch)"),
        base_sha=ParamSpec(
            "str", label="Base commit the files were made from (refused if the base moved)"
        ),
        files=ParamSpec("list", True, label="Files (path and content)", widget="json"),
        title=REQ_STR,
        body=TEXT,
        labels=ParamSpec("list"),
    ),
)

EVENT_TYPES = (
    "github.pr.opened",
    "github.pr.closed",
    "github.pr.reopened",
    "github.pr.synchronize",
    "github.pr.ready",
    "github.comment.created",
    "github.issue.opened",
    "github.review.submitted",
    "github.review_comment.created",
    "github.checks.suite_completed",
    "github.checks.workflow_completed",
    events.CHECKS_SETTLED_TYPE,
)

_EXTRA = {"needs_module": "cryptography", "extra": "github"}


def _port(module: str, attr: str) -> Lazy:
    return Lazy(f"{__name__}.{module}", attr, store=True)


class _SettleListener:
    """The checks settler as a node listener: settles the due SHAs each cycle."""

    def __init__(self, settler: Any) -> None:
        self.settler = settler

    def tick(self, report: Any) -> None:
        del report
        self.settler.tick()


def github_checks_settle(ctx: NodeContext) -> _SettleListener:
    """The ``github.checks_settle`` listener: placed, so this node settles only the SHAs
    whose App actor it can serve."""
    from culture_rules.extensions.github import checks_settle  # noqa: PLC0415

    lister = checks_settle.AppSuiteLister(
        ctx.store,
        secrets=ctx.resolve_secret,
        host=ctx.host,
        unresolved_retry_s=checks_settle.UNRESOLVED_RETRY_S,
    )
    return _SettleListener(
        checks_settle.ChecksSettler(
            ctx.store,
            lister.list_suites,
            pull=lister.get_pull,
            serves=lister.serves,
            clock=ctx.clock,
        )
    )


def github_router(store: Any) -> Any:
    """``POST /hooks/github``. Every GitHub read on the delivery path is bounded (suites, PR
    read and secret resolve within one budget); past it the SHA stays armed and the node's
    tick settles it."""
    from culture_rules.extensions.github import checks_settle, hooks  # noqa: PLC0415

    lister = checks_settle.AppSuiteLister(store)
    on_check = checks_settle.webhook_on_check(store, lister)

    def pull(repo: str, number: int) -> Any:  # read-only, bounded PR lookup for PR comments
        return lister.get_pull(repo, number, timeout_s=hooks.PULL_LOOKUP_TIMEOUT_S)

    return hooks.router(store, on_check=on_check, pull=pull)


def github_collections() -> tuple[str, ...]:
    from culture_rules.extensions.github.checks_settle import (  # noqa: PLC0415
        LATE_COLLECTION,
        RECOVERY_COLLECTION,
        SETTLE_COLLECTION,
    )
    from culture_rules.extensions.github.port import ONCE_COLLECTION  # noqa: PLC0415
    from culture_rules.extensions.github.pr_open import (  # noqa: PLC0415
        PR_OPEN_ONCE_COLLECTION,
    )

    return (
        SETTLE_COLLECTION,
        RECOVERY_COLLECTION,
        LATE_COLLECTION,
        ONCE_COLLECTION,
        PR_OPEN_ONCE_COLLECTION,
    )


def comment_params(params: Mapping[str, Any], path: str) -> list[Problem]:
    """``github.comment`` keeps no status board since 0.43.0: a ``status`` other than absent
    or false is refused when saved, as the port refuses it (``bad_input``) when it runs."""
    if params.get("status") in (None, False):
        return []
    return [
        (
            join(path, "status"),
            "action_param_removed",
            "github.comment has no status comment any more; drop params.status",
        )
    ]


def _literal(value: Any) -> str | None:
    """``value`` when it is a literal string (no template, no reference), else ``None``."""
    if not isinstance(value, str) or "{{" in value:
        return None
    if is_reference(value) or is_input_reference(value):
        return None
    return value


def pr_open_params(params: Mapping[str, Any], path: str) -> list[Problem]:
    """``github.pr_open``'s save-time checks on what is literal: the branch, the base, the
    base commit, the file list and its paths and the labels. A value a reference fills is
    checked by the port when it runs (the same limits, and the default branch, which only
    GitHub knows)."""
    from culture_rules.extensions.github import pr_open  # noqa: PLC0415

    out = _pr_open_branch_problems(params, path)
    out.extend(_pr_open_base_sha_problems(params, path))
    files = params.get("files")
    if isinstance(files, list):
        out.extend(_pr_open_files_problems(files, path))
    labels = params.get("labels")
    if isinstance(labels, list) and len(labels) > pr_open.MAX_LABELS:
        msg = f"at most {pr_open.MAX_LABELS} labels"
        out.append((join(path, "labels"), "too_many_labels", msg))
    return out


def _pr_open_branch_problems(params: Mapping[str, Any], path: str) -> list[Problem]:
    """The literal ``branch`` and ``base``: each a branch name, and not the same branch."""
    from culture_rules.extensions.github.client import is_branch_name  # noqa: PLC0415

    out: list[Problem] = []
    branch, base = _literal(params.get("branch")), _literal(params.get("base"))
    for name, value in (("branch", branch), ("base", base)):
        if value and not is_branch_name(value):
            out.append((join(path, name), "invalid_branch", f"{value!r} is not a branch name"))
    if branch and base and branch.casefold() == base.casefold():
        out.append((join(path, "branch"), "branch_is_base", "the new branch is the base branch"))
    return out


def _pr_open_base_sha_problems(params: Mapping[str, Any], path: str) -> list[Problem]:
    """The literal ``base_sha``: empty (not given) or a full 40-hex commit sha."""
    from culture_rules.extensions.github.client import _is_full_sha  # noqa: PLC0415

    sha = _literal(params.get("base_sha"))
    if not sha or _is_full_sha(sha):
        return []
    return [(join(path, "base_sha"), "invalid_sha", f"{sha!r} is not a full commit sha")]


def _pr_open_files_problems(files: list[Any], path: str) -> list[Problem]:
    """The literal file list: its length, each item's shape and each literal path."""
    from culture_rules.extensions.github import pr_open  # noqa: PLC0415

    out: list[Problem] = []
    if len(files) > pr_open.MAX_FILES:
        msg = f"at most {pr_open.MAX_FILES} files"
        out.append((join(path, "files"), "too_many_files", msg))
    for i, item in enumerate(files):
        out.extend(_pr_open_file_problems(item, join(path, f"files.{i}")))
    return out


def _pr_open_file_problems(item: Any, where: str) -> list[Problem]:
    """One file of the list: ``{path, content}`` (a string is a reference that fills it),
    with a literal path that is not refused."""
    from culture_rules.extensions.github import pr_open  # noqa: PLC0415

    if isinstance(item, str):
        return []  # a reference fills it
    if not isinstance(item, Mapping) or not {"path", "content"} <= set(item):
        return [(where, "action_param_type", "each file is {path, content}")]
    name = _literal(item.get("path"))
    if name is not None and pr_open.path_refusal(name):
        return [(join(where, "path"), "path_refused", f"{name!r} is refused")]
    return []


def pr_open_guard(params: Mapping[str, Any]) -> list[str]:
    """``github.pr_open``'s fence words."""
    del params
    return ["(a new branch, never the default branch; once per step)"]


def comment_guard(params: Mapping[str, Any]) -> list[str]:
    """``github.comment``'s guard words: once per key."""
    return ["(once per once_key)"] if params.get("once_key") else []


def merge_guard(params: Mapping[str, Any]) -> list[str]:
    """``github.merge``'s fence: the port runs it only from a click with the button's code."""
    del params
    return ["(only on a button click, with the button's one-time code)"]


def register(ext: Extension) -> None:
    """GitHub (as a GitHub App, the ``github`` extra): comment, merge, PR facts, opening a PR,
    the head lookup, ``POST /hooks/github``, the checks settler and their event types."""
    for k in KINDS:
        ext.action_kind(k)
    ext.params_validator(GITHUB_COMMENT, comment_params)
    ext.action_phrases(GITHUB_COMMENT, after=comment_guard)
    ext.action_phrases(GITHUB_MERGE, after=merge_guard)
    ext.action_phrases(GITHUB_PR)
    ext.params_validator(GITHUB_PR_OPEN, pr_open_params)
    ext.action_phrases(GITHUB_PR_OPEN, words=(("repo", "on {}"),), after=pr_open_guard)
    ext.action_port("github.pr_head", _port("port", "GitHubPrHeadPort"), **_EXTRA)
    ext.action_port(GITHUB_COMMENT, _port("port", "GitHubCommentPort"), **_EXTRA)
    ext.action_port(GITHUB_PR, _port("port", "GitHubPrPort"), **_EXTRA)
    ext.action_port(GITHUB_MERGE, _port("merge", "GitHubMergePort"), **_EXTRA)
    ext.action_port(GITHUB_PR_OPEN, _port("pr_open", "GitHubPrOpenPort"), **_EXTRA)
    for event in EVENT_TYPES:
        ext.event_type(event)
    ext.event_reservation("github.reaction", events.reaction_reserved)
    ext.event_reservation("github.settle", events.settle_reserved)
    ext.webhook_route("/hooks/github", github_router)
    ext.node_listener("github.checks_settle", github_checks_settle)
    ext.node_collections(github_collections)
