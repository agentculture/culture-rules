"""The ``jira`` extension: ``jira.comment``, ``POST /hooks/jira`` and its event types.

The port is :class:`culture_rules.extensions.jira.port.JiraCommentPort`, the client
:class:`culture_rules.extensions.jira.client.JiraClient` and the webhook receiver
:func:`culture_rules.extensions.jira.hooks.router` (the ``server`` extra, imported when the
API mounts it). Registering imports nothing but the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, kind

__all__ = ["EVENT_TYPES", "KINDS", "register"]

KINDS = (
    kind("jira.comment", "Comment on a Jira issue", actor=REQ_STR, issue=REQ_STR, body=REQ_STR),
)

EVENT_TYPES = ("jira.issue.created", "jira.issue.updated", "jira.comment.created")


def register(ext: Extension) -> None:
    """``jira.comment`` (``port.JiraCommentPort``), ``POST /hooks/jira`` (``hooks.router``)
    and the event types the receiver emits."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("jira.comment", Lazy(__name__ + ".port", "JiraCommentPort", store=True))
    for event in EVENT_TYPES:
        ext.event_type(event)
    ext.webhook_route("/hooks/jira", Lazy(__name__ + ".hooks", "router", store=True))
