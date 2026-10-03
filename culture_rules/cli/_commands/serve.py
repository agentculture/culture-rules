"""``culture-rules serve`` — run the HTTP API (needs the ``server`` extra)."""

from __future__ import annotations

import argparse

from culture_rules.cli._errors import EXIT_ENV_ERROR, CliError
from culture_rules.cli._output import emit_diagnostic, emit_result


def cmd_serve(args: argparse.Namespace) -> int:
    from culture_rules.server import serve as serve_mod  # noqa: PLC0415 - optional extra

    try:
        emit_diagnostic(f"serving the culture-rules API on {args.host or 'default host'}")
        serve_mod.serve(
            host=args.host,
            port=args.port,
            admins=tuple(args.admin or ()),
            node_name=args.node_name,
        )
    except serve_mod.ServerExtraMissing as exc:
        raise CliError(EXIT_ENV_ERROR, str(exc), "pip install 'culture-rules[server]'") from exc
    except ImportError as exc:  # the store extra (pymongo) when no store is injected
        raise CliError(EXIT_ENV_ERROR, str(exc), "pip install 'culture-rules[store]'") from exc
    if getattr(args, "json", False):
        emit_result({"served": True}, json_mode=True)
    return 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("serve", help="Run the HTTP API (needs the 'server' extra).")
    p.add_argument("--host", help="bind address (default CULTURE_RULES_HOST or 127.0.0.1)")
    p.add_argument("--port", type=int, help="port (default CULTURE_RULES_PORT or 8765)")
    p.add_argument("--admin", action="append", help="identity allowed to purge (repeatable)")
    p.add_argument(
        "--node-name",
        help="engine node /health reports on (default CULTURE_RULES_NODE_NAME or short hostname)",
    )
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=cmd_serve)
