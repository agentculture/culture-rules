"""The ``machine`` extension: ``machine.command``, a registered command on a runner actor.

The port is :class:`culture_rules.extensions.machine.port.MachineCommandPort` (registered
commands only, argv templates, never a shell). Registering imports nothing but the kind's
typed params.
"""

from __future__ import annotations

from culture_rules.extensions.registry import Extension, Lazy
from culture_rules.model.action_kinds import REQ_STR, ParamSpec, kind

__all__ = ["KINDS", "register"]

KINDS = (
    kind(
        "machine.command",
        "Run a command on a machine actor",
        label="Run a command on a machine",
        actors=({"kinds": ["runner"]},),
        actor=REQ_STR,
        command=ParamSpec("str", True, widget="command"),
        args=ParamSpec("dict", widget="command_args", args_for="command"),
    ),
)


def register(ext: Extension) -> None:
    """``machine.command`` (``port.MachineCommandPort``)."""
    for k in KINDS:
        ext.action_kind(k)
    ext.action_phrases("machine.command", words=(("command", "`{}`"),))
    ext.action_port("machine.command", Lazy(__name__ + ".port", "MachineCommandPort", store=True))
