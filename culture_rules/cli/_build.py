"""Builds the argparse tree for a noun from the command registry and dispatches to handlers."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from typing import Any

from culture_rules.cli import _api
from culture_rules.cli._errors import EXIT_USER_ERROR, CliError
from culture_rules.cli._output import emit_result
from culture_rules.cli.registry import Context, Param, Verb


def parse_object(value: str) -> Any:
    """``--flag`` value for an object param: inline JSON, ``@path`` or ``-`` (stdin)."""
    try:
        if value == "-":
            text = sys.stdin.read()
        elif value.startswith("@"):
            with open(value[1:], encoding="utf-8") as fh:
                text = fh.read()
        else:
            text = value
        return json.loads(text)
    except (OSError, ValueError) as exc:
        raise CliError(
            EXIT_USER_ERROR,
            f"not valid JSON: {exc}",
            "pass inline JSON, @path/to/file.json, or - for stdin",
        ) from exc


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _add_param(p: argparse.ArgumentParser, param: Param) -> None:
    if param.positional:
        kw: dict[str, Any] = {"help": param.help, "metavar": param.name.upper()}
        if not param.required:
            kw["nargs"] = "?"
        p.add_argument(param.name, **kw)
        return
    if param.type == "boolean":
        p.add_argument(_flag(param.name), dest=param.name, action="store_true", help=param.help)
        return
    kw = {"dest": param.name, "help": param.help, "required": param.required}
    if param.type == "integer":
        kw["type"] = int
    if param.type == "object":
        kw["metavar"] = "JSON|@FILE|-"
    if param.type == "array":
        kw["action"] = "append"
    if param.default is not None:
        kw["default"] = param.default
    p.add_argument(_flag(param.name), **kw)


def render_text(result: Any) -> str:
    if isinstance(result, dict) and "sections" in result and "subject" in result:
        from culture_rules.cli._commands.overview import render_text as render  # noqa: PLC0415

        return render(result["subject"], result["sections"])
    if isinstance(result, dict) and isinstance(result.get("lines"), list):
        return "\n".join(str(x) for x in result["lines"])  # a verb that renders itself
    if isinstance(result, dict) and result.get("dry_run"):
        would = result.get("would") or {}
        if would.get("method"):
            head = f"dry-run: {result.get('verb')} would {would['method']} {would.get('path')}"
        else:  # the server ran the dry-run itself (e.g. the migrations)
            head = f"dry-run: {result.get('verb')} (nothing was changed)"
        return head + "\n" + json.dumps(result, indent=2) + "\nre-run with --apply to commit"
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        lines = [f"{len(result['items'])} item(s)"]
        for item in result["items"]:
            extra = " ".join(
                f"{k}={item[k]}" for k in ("status", "enabled", "rule_id") if k in item
            )
            lines.append(f"- {item.get('id', '?')} {extra}".rstrip())
        return "\n".join(lines)
    if isinstance(result, dict) and isinstance(result.get("files"), dict):
        return "\n".join(f"# {k}\n{v}" for k, v in result["files"].items())
    return json.dumps(result, indent=2, ensure_ascii=False)


def _handler(verb: Verb) -> Callable[[argparse.Namespace], int]:
    def run(args: argparse.Namespace) -> int:
        params: dict[str, Any] = {}
        for param in verb.params:
            value = getattr(args, param.name, None)
            if param.type == "object" and isinstance(value, str):
                value = parse_object(value)
            if value is None and param.type == "boolean":
                value = False
            if value is not None:
                params[param.name] = value
        ctx = Context(
            client=_api.make_client(getattr(args, "api_url", None)),
            apply=bool(getattr(args, "apply", False)),
        )
        result = verb.handler(ctx, **params)
        json_mode = bool(getattr(args, "json", False))
        emit_result(result if json_mode else render_text(result), json_mode=json_mode)
        return 0

    return run


def register_noun(sub: argparse._SubParsersAction, noun: str, help_: str) -> None:
    """Add ``noun`` with one subcommand per registered verb (read from the registry)."""
    from culture_rules.cli.verbs import REGISTRY  # noqa: PLC0415

    verbs = REGISTRY.verbs(noun)
    p = sub.add_parser(noun, help=help_)
    p.add_argument("--json", action="store_true", help="Emit structured JSON.")
    p.add_argument("--api-url", help="API base URL (default CULTURE_RULES_API_URL).")
    overview = next(v for v in verbs if v.name == "overview")
    p.set_defaults(func=_handler(overview), apply=False)
    noun_sub = p.add_subparsers(dest=f"{noun}_command", parser_class=type(p))
    for verb in verbs:
        vp = noun_sub.add_parser(verb.name, help=verb.summary)
        for param in verb.params:
            _add_param(vp, param)
        if verb.mutating:
            vp.add_argument(
                "--apply", action="store_true", help="Commit the write (default: dry-run)."
            )
        # SUPPRESS: when absent here, the noun-level value (`rules --json list`) stands
        vp.add_argument(
            "--json", action="store_true", default=argparse.SUPPRESS, help="Emit structured JSON."
        )
        vp.add_argument(
            "--api-url",
            default=argparse.SUPPRESS,
            help="API base URL (default CULTURE_RULES_API_URL).",
        )
        vp.set_defaults(func=_handler(verb))
