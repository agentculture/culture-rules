"""The ``jira`` extension: ``jira.comment``, ``jira.read``, ``POST /hooks/jira``, events, filters.

The ports are :class:`culture_rules.extensions.jira.port.JiraCommentPort` and
:class:`culture_rules.extensions.jira.read.JiraReadPort`, the client
:class:`culture_rules.extensions.jira.client.JiraClient` and the webhook receiver
:func:`culture_rules.extensions.jira.hooks.router` (the ``server`` extra, imported when the
API mounts it). Registering imports nothing but the kinds' typed params and the stdlib
trigger filter :mod:`culture_rules.extensions.jira.filters`.
"""

from __future__ import annotations

from culture_rules.extensions.jira import filters
from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, REQ_TEXT, ParamSpec, kind

__all__ = ["EVENT_TYPES", "KINDS", "register"]

JIRA_COMMENT = "jira.comment"
JIRA_READ = "jira.read"

KINDS = (
    kind(
        JIRA_COMMENT,
        "Comment on a Jira issue",
        label="Comment on Jira",
        declarable_by_apps=True,
        actor=REQ_STR,
        issue=REQ_STR,
        body=REQ_TEXT,
    ),
    kind(
        JIRA_READ,
        "Read a Jira issue: its description, comments and named fields",
        label="Read a Jira issue",
        declarable_by_apps=True,
        actor=REQ_STR,
        issue=REQ_STR,
        fields=ParamSpec("list"),  # custom field names (or ids) to include, optional
    ),
)

EVENT_TYPES = ("jira.issue.created", "jira.issue.updated", "jira.comment.created")


def register(ext: Extension) -> None:
    """``jira.comment`` (``port.JiraCommentPort``), ``jira.read`` (``read.JiraReadPort``),
    ``POST /hooks/jira`` (``hooks.router``), the event types the receiver emits and the
    ``jira.*`` trigger filter (``filters``: ``users``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_phrases(JIRA_COMMENT, words=(("issue", "on {}"),))
    ext.action_phrases(JIRA_READ, words=(("issue", "of {}"),))
    ext.action_port(JIRA_COMMENT, Lazy(__name__ + ".port", "JiraCommentPort", store=True))
    ext.action_port(JIRA_READ, Lazy(__name__ + ".read", "JiraReadPort", store=True))
    for event in EVENT_TYPES:
        ext.event_type(event)
    ext.trigger_filter(
        "jira", match=filters.admits, validate=filters.problems, describe=filters.words
    )
    ext.webhook_route("/hooks/jira", Lazy(__name__ + ".hooks", "router", store=True))
