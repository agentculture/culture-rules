"""Built-in ``select``: one step's outputs from whichever of a workflow's paths ran (#39 t4).

pr-fix runs one agent path per try: ``agent_qwen``; ``agent_claude`` and the Claude hand-off
(#56); or ``agent_codex`` and the Codex hand-off, each guarded by ``when`` (sources ``qwen``,
``claude`` and ``codex``, each labelled with its fixer actor). The gate must read its
``worktree``, ``start_sha`` and ``commit_sha`` from **one** source step: two edges into one
port would leave the value to edge order (the last edge with a value wins), which is
fragile. ``select`` is that source.

Config: ``sources`` - ``{prefix: label}``, one entry per path (a prefix is a short
identifier; the label names the path, for pr-fix the fixer actor); ``ports`` - the names
every path must supply; ``optional`` (default none) - names passed through when the
selected path has them. Inputs are ``<prefix>_<name>``, each wired from its own path's step.

A path **ran** when any of its ``ports`` inputs has a value (a skipped step has no
outputs, so its edges carry nothing). Exactly one path must have run, and it must supply
every one of ``ports``. Outputs: each of ``ports`` and ``optional`` (``None`` when absent)
plus ``source`` (the selected path's label). Otherwise the step fails non-retryably:
``select_none`` (no path ran: for pr-fix, a ``fixer`` no agent step takes), ``select_ambiguous``
(more than one ran), ``select_incomplete`` (the path that ran left a port out) or
``bad_config``. No I/O; standard-library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from culture_rules.engine.actorport import InvocationContext, InvocationResult

__all__ = ["SELECT_BUILTIN", "SOURCE_OUTPUT", "SelectPort"]

SELECT_BUILTIN = "select"
SOURCE_OUTPUT = "source"
"""The output naming the selected path's label."""
_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")
_BAD_CONFIG = "bad_config"


class _Refused(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")


def _names(config: Mapping[str, Any], key: str, *, required: bool) -> list[str]:
    value = config.get(key, None if required else [])
    if not isinstance(value, list) or (required and not value):
        raise _Refused(_BAD_CONFIG, f"config.{key} must be a list of port names")
    if not all(isinstance(n, str) and _NAME.fullmatch(n) for n in value):
        raise _Refused(_BAD_CONFIG, f"config.{key} names must be short identifiers")
    return list(value)


def _config(config: Mapping[str, Any]) -> tuple[dict[str, str], list[str], list[str]]:
    sources = config.get("sources")
    if not isinstance(sources, Mapping) or not sources:
        raise _Refused(_BAD_CONFIG, "config.sources must map each path's prefix to its label")
    for prefix, label in sources.items():
        if not (isinstance(prefix, str) and _NAME.fullmatch(prefix)):
            raise _Refused(_BAD_CONFIG, f"source prefix {prefix!r} is not a short identifier")
        if not isinstance(label, str) or not label:
            raise _Refused(_BAD_CONFIG, f"source {prefix!r} needs a label")
    ports = _names(config, "ports", required=True)
    optional = _names(config, "optional", required=False)
    if set(ports) & set(optional) or SOURCE_OUTPUT in (*ports, *optional):
        raise _Refused(_BAD_CONFIG, "a port is listed twice, or is named source")
    return dict(sources), ports, optional


def select(input: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    """The selected path's outputs (module doc), else :class:`_Refused`."""
    sources, ports, optional = _config(config)
    ran = [
        prefix for prefix in sources if any(input.get(f"{prefix}_{p}") is not None for p in ports)
    ]
    if not ran:
        raise _Refused("select_none", "no path ran (every one was skipped)")
    if len(ran) > 1:
        raise _Refused("select_ambiguous", f"more than one path ran: {', '.join(ran)}")
    (prefix,) = ran
    missing = [p for p in ports if input.get(f"{prefix}_{p}") is None]
    if missing:
        raise _Refused("select_incomplete", f"path {prefix} left out {', '.join(missing)}")
    out = {name: input.get(f"{prefix}_{name}") for name in (*ports, *optional)}
    out[SOURCE_OUTPUT] = sources[prefix]
    return out


class SelectPort:
    """Built-in ``select`` (see the module docstring). No I/O."""

    supports_idempotency_key = True

    def invoke(
        self,
        input: Mapping[str, Any],
        _idempotency_key: str,
        _deadline: datetime,
        *,
        context: InvocationContext,
    ) -> InvocationResult:
        config = context.config if isinstance(context.config, Mapping) else {}
        try:
            return InvocationResult.completed(select(input, config))
        except _Refused as exc:
            return InvocationResult.failed(str(exc), retryable=False)
