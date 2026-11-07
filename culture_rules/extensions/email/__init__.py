"""The ``email`` extension: ``email.send`` through an email app actor (Resend).

The port is :class:`culture_rules.extensions.email.port.EmailSendPort` (recipient allowlist,
sender domain) and the client :class:`culture_rules.extensions.email.resend.ResendClient`.
Registering imports nothing but the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import ADV, REQ_STR, TEXT, ParamSpec, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "email.send",
        "Send an email through an email app actor (Resend)",
        label="Send an email",
        declarable_by_apps=True,
        actors=({"kinds": ["app"], "surface": "email"},),
        actor=REQ_STR,
        to=ParamSpec("list", True),  # each must be on the actor's recipient allowlist
        subject=REQ_STR,
        html=TEXT,  # at least one of html/text: the port checks it
        text=TEXT,
        idempotency_key=ADV,  # replaces the engine's per-run key (a durable, per-date key)
        sender=ADV,  # replaces the actor's from address; must be at the actor's domain
        not_after=ADV,  # ISO-8601: at or past it the send fails email_window_passed, no request
    ),
)


def register(ext: Extension) -> None:
    """``email.send`` (``port.EmailSendPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("email.send", Lazy(__name__ + ".port", "EmailSendPort", store=True))
