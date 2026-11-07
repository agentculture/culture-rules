"""The ``github`` extension: GitHub as a GitHub App (the ``github`` extra, ``cryptography``).

* action kinds ``github.comment`` and ``github.pr`` and the head lookup ``github.pr_head``
  (:mod:`~culture_rules.extensions.github.port`), and ``github.merge``
  (:mod:`~culture_rules.extensions.github.merge`, fenced by an operator's click carrying the
  button's one-time code from the ``discord`` extension), over the App client
  :mod:`~culture_rules.extensions.github.client`;
* ``POST /hooks/github`` (:mod:`~culture_rules.extensions.github.hooks`) and the checks
  settler (:mod:`~culture_rules.extensions.github.checks_settle`, a node listener and the
  webhook's settle-on-check), their event types and collections;
* :mod:`~culture_rules.extensions.github.public_text`, untrusted text made inert for a public
  comment.

Registering imports nothing but the kinds' typed params; without ``cryptography`` the ports
are ``extra_missing``.
"""

from __future__ import annotations

from typing import Any

from culture_rules.extensions.registry import Extension, Lazy, NodeContext
from culture_rules.model.action_kinds import ADV, REQ_INT, REQ_STR, REQ_TEXT, ParamSpec, kind

__all__ = ["EVENT_TYPES", "KINDS", "register"]

KINDS = (
    kind(
        "github.comment",
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
        "github.merge",
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
        "github.pr",
        "Read one PR's title, link, author, draft flag, state, repos, head branch and sha"
        " as the GitHub App",
        label="Read a GitHub PR",
        declarable_by_apps=True,
        actor=REQ_STR,
        repo=REQ_STR,
        number=REQ_INT,
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
    "github.pr.checks_settled",
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

    return (SETTLE_COLLECTION, RECOVERY_COLLECTION, LATE_COLLECTION, ONCE_COLLECTION)


def register(ext: Extension) -> None:
    """GitHub (as a GitHub App, the ``github`` extra): comment, merge and PR facts, the head
    lookup, ``POST /hooks/github``, the checks settler and their event types."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("github.pr_head", _port("port", "GitHubPrHeadPort"), **_EXTRA)
    ext.action_port("github.comment", _port("port", "GitHubCommentPort"), **_EXTRA)
    ext.action_port("github.pr", _port("port", "GitHubPrPort"), **_EXTRA)
    ext.action_port("github.merge", _port("merge", "GitHubMergePort"), **_EXTRA)
    for event in EVENT_TYPES:
        ext.event_type(event)
    ext.webhook_route("/hooks/github", github_router)
    ext.node_listener("github.checks_settle", github_checks_settle)
    ext.node_collections(github_collections)
