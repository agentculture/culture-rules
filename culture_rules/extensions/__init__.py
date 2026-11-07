"""Extensions: how apps plug action kinds, built-ins, events, webhooks and listeners in.

:func:`registry` is the engine's registry, with the built-in apps
(:mod:`culture_rules.extensions.builtin`) installed on first use. Core reads it - the action
catalogue (``model.action_kinds``), the node's ports (``node.runner.default_ports``), the
API's webhook routes (``server.app``) and the node's listeners and collections
(``node.daemon``) - and never imports an app to learn what exists. Another extension
installs itself the same way::

    from culture_rules.extensions import Lazy, registry

    def register(ext):
        ext.action_kind(ActionKind("acme.ping", {"actor": ParamSpec("str", True)}, "Ping"))
        ext.action_port("acme.ping", Lazy("acme_rules.ports", "PingPort", store=True))

    registry().install("acme", register)

Standard library only (see :mod:`culture_rules.extensions.registry`).
"""

from __future__ import annotations

import threading

from culture_rules.extensions.registry import (
    EVENT_TYPE_RE,
    RESERVED_BUILTINS,
    WEBHOOK_PREFIX,
    ActionPortEntry,
    BuiltinEntry,
    EventTypeEntry,
    Extension,
    ExtensionError,
    KindEntry,
    Lazy,
    NodeContext,
    NodeListenerEntry,
    Registry,
    WebhookRouteEntry,
)

__all__ = [
    "EVENT_TYPE_RE",
    "RESERVED_BUILTINS",
    "WEBHOOK_PREFIX",
    "ActionPortEntry",
    "BuiltinEntry",
    "EventTypeEntry",
    "Extension",
    "ExtensionError",
    "KindEntry",
    "Lazy",
    "NodeContext",
    "NodeListenerEntry",
    "Registry",
    "WebhookRouteEntry",
    "registry",
]

_REGISTRY = Registry()
_LOAD_LOCK = threading.RLock()
_state = {"loaded": False, "loading": False}


def registry() -> Registry:
    """The engine's registry, the built-in apps installed (once, on the first call)."""
    if _state["loaded"]:
        return _REGISTRY
    with _LOAD_LOCK:
        if _state["loaded"] or _state["loading"]:  # loading: a re-entrant read mid-install
            return _REGISTRY
        _state["loading"] = True
        try:
            from culture_rules.extensions.builtin import install_builtins  # noqa: PLC0415

            install_builtins(_REGISTRY)
            _state["loaded"] = True
        finally:
            _state["loading"] = False
    return _REGISTRY
