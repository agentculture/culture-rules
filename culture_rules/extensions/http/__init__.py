"""The ``http`` extension: ``http.call``, one HTTP request through the bound actor's allowlist.

The port is :class:`culture_rules.extensions.http.port.HttpCallPort`; its vetted opener
(destination policy, pinned connections, no redirects) is also what the Discord attachment
fetch and the library push reuse. Registering imports nothing but the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "http.call",
        "Call an HTTP endpoint",
        actor=REQ_STR,
        method=REQ_STR,
        url=REQ_STR,
        headers=ParamSpec("dict"),
        body=ParamSpec("any"),
        save_to=STR,  # an absolute dir the actor's http.save_roots lists: stream the body there
    ),
)


def register(ext: Extension) -> None:
    """``http.call`` (``port.HttpCallPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("http.call", Lazy(__name__ + ".port", "HttpCallPort", store=True))
