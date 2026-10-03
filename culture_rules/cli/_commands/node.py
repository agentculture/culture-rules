"""``culture-rules node run`` — run this host's engine node (the rules engine itself).

Unlike every other noun, ``node`` talks to the store directly: the node *is* the engine,
not an API client. :mod:`culture_rules.node` is imported lazily inside the handler, so
importing the CLI never loads the engine or a store driver.
"""

from __future__ import annotations

import argparse

from culture_rules.cli._errors import EXIT_ENV_ERROR, CliError
from culture_rules.cli._output import emit_diagnostic, emit_result


def _text(summary: dict) -> str:
    report = summary.get("report")
    if report is None:
        return f"node {summary['host']}: {summary['cycles']} cycles"
    return (
        f"node {summary['host']}: 1 cycle; ingested {report['ingested']}, "
        f"evaluated {len(report['evaluated'])}, deferred {len(report['deferred'])}, "
        f"started {len(report['started'])}, transitions {report['transitions']}, "
        f"errors {len(report['errors'])}"
    )


def cmd_node_run(args: argparse.Namespace) -> int:
    from culture_rules.node import runner  # noqa: PLC0415 - lazy (the engine not the API)

    try:
        summary = runner.run_node(args.host, once=args.once, idle=args.idle)
    except runner.NodeSetupError as exc:
        raise CliError(
            EXIT_ENV_ERROR,
            str(exc),
            "set CULTURE_RULES_MONGO_URI (and install 'culture-rules[store]')",
        ) from exc
    if not summary.get("events"):
        emit_diagnostic(
            "no event source (events-cli missing or its subscriptions could not be set up; "
            "see the warning): nothing is ingested"
        )
    emit_result(summary if args.json else _text(summary), json_mode=args.json)
    errors = (summary.get("report") or {}).get("errors") or summary.get("errors") or []
    return EXIT_ENV_ERROR if args.once and errors else 0


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser("node", help="Run this host's engine node (talks to the store).")
    verbs = p.add_subparsers(dest="node_command", parser_class=type(p))
    run = verbs.add_parser("run", help="Run the engine node loop (or one cycle with --once).")
    run.add_argument(
        "--host",
        help="this node's machine name (default CULTURE_RULES_NODE_NAME or the short hostname)",
    )
    run.add_argument("--once", action="store_true", help="run one full cycle and exit")
    run.add_argument("--idle", type=float, default=1.0, help="pause between cycles (seconds)")
    run.add_argument("--json", action="store_true", help="Emit structured JSON.")
    run.set_defaults(func=cmd_node_run)

    def _missing(args: argparse.Namespace) -> int:
        raise CliError(1, "missing verb for 'node'", "run 'culture-rules node run --help'")

    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.set_defaults(func=_missing)
