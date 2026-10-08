"""Registry verbs as MCP tools, with no third-party imports.

One tool per registry verb, named ``<noun>_<verb>`` with ``params_schema()`` as its input
schema. A mutating tool only commits when ``apply`` is exactly ``true``; otherwise the same
handler runs with ``Context(apply=False)`` and returns the dry-run envelope. Handlers go through
the HTTP API client, so this layer never touches the store or the engine.
"""

from __future__ import annotations

from typing import Any

from culture_rules.cli._errors import CliError
from culture_rules.cli.registry import Context, Verb
from culture_rules.cli.verbs import REGISTRY

__all__ = ["ToolError", "call_tool", "tool_specs"]


class ToolError(Exception):
    """A tool call that failed; the message is shown to the calling model."""


def _by_tool_name() -> dict[str, Verb]:
    return {v.tool_name: v for v in REGISTRY.verbs()}


def tool_specs() -> list[dict[str, Any]]:
    """``{name, description, inputSchema}`` for every registry verb, in registry order."""
    out = []
    for v in REGISTRY.verbs():
        note = " Writes are a dry-run unless apply=true." if v.mutating else ""
        out.append(
            {
                "name": v.tool_name,
                "description": f"{v.summary}{note} (requires role {v.role})",
                "inputSchema": v.params_schema(),
            }
        )
    return out


def call_tool(name: str, arguments: dict[str, Any] | None, client: Any) -> Any:
    """Run tool ``name`` against ``client`` (an API client) and return the handler's result."""
    verb = _by_tool_name().get(name)
    if verb is None:
        raise ToolError(f"unknown tool {name!r}")
    args = dict(arguments or {})
    apply = args.pop("apply", False) is True and verb.mutating
    known = {p.name for p in verb.params}
    extra = sorted(set(args) - known)
    if extra:
        raise ToolError(f"{name}: unexpected argument(s) {', '.join(extra)}")
    missing = [
        p.name
        for p in verb.params
        if p.required and (p.name not in args if p.type == "any" else args.get(p.name) is None)
    ]
    if missing:
        raise ToolError(f"{name}: missing required argument(s) {', '.join(missing)}")
    any_typed = {p.name for p in verb.params if p.type == "any"}  # an explicit null is a value
    params = {k: v for k, v in args.items() if v is not None or k in any_typed}
    try:
        return verb.handler(Context(client=client, apply=apply), **params)
    except CliError as exc:
        raise ToolError(f"{exc.message} (hint: {exc.remediation})") from exc
