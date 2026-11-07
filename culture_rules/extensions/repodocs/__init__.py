"""The ``repodocs`` extension: ``repodocs.sync``, every repo's devague record to the library.

The port is :class:`culture_rules.extensions.repodocs.port.RepodocsSyncPort` (git as argv
lists, a state file, counts only); it pushes through the ``library`` extension's transport.
Registering imports nothing but the kind's typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "repodocs.sync",
        "Push every repo's specs, plans, deliveries, designs and .devague files to the library",
        actor=REQ_STR,  # a service actor: base_url, api_key, repos_root, state_path, limits
    ),
)


def register(ext: Extension) -> None:
    """``repodocs.sync`` (``port.RepodocsSyncPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_port("repodocs.sync", Lazy(__name__ + ".port", "RepodocsSyncPort", store=True))
