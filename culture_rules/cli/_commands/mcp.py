"""``culture-rules mcp`` — serve the command registry as MCP tools over stdio (``mcp`` extra)."""

from __future__ import annotations

import argparse

from culture_rules.cli._errors import EXIT_ENV_ERROR, CliError
from culture_rules.cli._output import emit_diagnostic


def cmd_mcp(args: argparse.Namespace) -> int:
    """Run until the MCP client disconnects. Stdout is the protocol channel, so nothing else
    is ever written there; ``--json`` only shapes the error on stderr."""
    from culture_rules.mcp import server as mcp_server  # noqa: PLC0415 - SDK loads lazily

    try:
        if not getattr(args, "json", False):
            emit_diagnostic("serving culture-rules MCP tools over stdio")
        mcp_server.run_stdio()
    except mcp_server.ServerExtraMissing as exc:
        raise CliError(EXIT_ENV_ERROR, str(exc), "pip install 'culture-rules[mcp]'") from exc
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "mcp", help="Serve the CLI verbs as MCP tools over stdio (needs the 'mcp' extra)."
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_mcp)
