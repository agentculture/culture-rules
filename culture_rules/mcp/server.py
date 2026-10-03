"""The MCP server: low-level ``mcp`` SDK server whose tools come from the registry.

Needs ``pip install 'culture-rules[mcp]'``; the SDK is imported lazily inside the functions so
``import culture_rules.mcp`` never pulls it in. Credentials come from the same environment as
the CLI (``CULTURE_RULES_API_URL``, ``CULTURE_RULES_TOKEN``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from culture_rules.mcp import tools

__all__ = ["ServerExtraMissing", "build_server", "run_stdio"]


class ServerExtraMissing(ImportError):
    """The ``mcp`` extra is not installed."""


_EXTRA_MESSAGE = "the MCP server needs the 'mcp' extra: pip install 'culture-rules[mcp]'"


def _sdk():
    try:
        import mcp.types as types  # noqa: PLC0415 - optional extra
        from mcp.server.lowlevel import Server  # noqa: PLC0415
    except ImportError as exc:
        raise ServerExtraMissing(_EXTRA_MESSAGE) from exc
    return Server, types


def build_server(client_factory: Callable[[], Any] | None = None):
    """An MCP ``Server`` exposing every registry verb; ``client_factory`` yields the API client."""
    Server, types = _sdk()
    if client_factory is None:
        from culture_rules.cli import _api  # noqa: PLC0415

        client_factory = _api.make_client
    server = Server("culture-rules")

    @server.list_tools()
    async def _list() -> list[Any]:
        return [types.Tool(**spec) for spec in tools.tool_specs()]

    @server.call_tool(validate_input=False)
    async def _call(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        try:
            result = tools.call_tool(name, arguments, client_factory())
        except tools.ToolError as exc:
            raise ValueError(str(exc)) from exc  # the SDK turns this into isError
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

    return server


def run_stdio() -> None:
    """Serve over stdio until the client disconnects.

    Raises :class:`ServerExtraMissing` when the ``mcp`` extra (the SDK and its ``anyio``)
    is not installed, before anything is served.
    """
    try:
        import anyio  # noqa: PLC0415 - optional extra (an mcp dependency)
        from mcp.server.stdio import stdio_server  # noqa: PLC0415
    except ImportError as exc:
        raise ServerExtraMissing(_EXTRA_MESSAGE) from exc

    server = build_server()

    async def main() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(main)
