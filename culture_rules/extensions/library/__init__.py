"""The ``library`` extension: ``library.push``, a file to the library its actor describes.

The port is :class:`culture_rules.extensions.library.port.LibraryPushPort` (upload, replace
by ``item_id``, an optional device queue); its multipart encoder and transport are what the
``stt`` and ``repodocs`` extensions reuse. Registering imports nothing but the kind's typed
params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "library.push",
        "Upload a file to a library, or replace one by id (and queue it on a device queue)",
        any_of=(("content_base64", "content_path"),),
        actor=REQ_STR,
        content_base64=STR,  # or content_path + content_root (d7): one of the two is required
        content_path=STR,  # a file under content_root on the node; its bytes never enter run state
        content_root=STR,
        title=REQ_STR,
        kind=REQ_STR,  # one of the actor's params.kinds
        visibility=STR,  # one of the actor's params.visibilities
        author=STR,
        device_id=STR,  # used only when the actor has a queue_path
        format=STR,  # one of the actor's params.formats; epub when unset
        item_id=ParamSpec("int"),  # replace this item through the actor's replace_path
    ),
)


def register(ext: Extension) -> None:
    """``library.push`` (``port.LibraryPushPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("library.push", Lazy(__name__ + ".port", "LibraryPushPort", store=True))
