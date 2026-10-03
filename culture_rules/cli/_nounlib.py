"""Shared helpers for the noun modules: the dry-run write envelope and definition verbs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from culture_rules.cli import _api
from culture_rules.cli._errors import EXIT_USER_ERROR, CliError
from culture_rules.cli.registry import Context, Param, Verb

ID = Param("id", help="identifier of the item", required=True, positional=True)
BODY = Param("body", type="object", help="the definition (JSON)", required=True)
_SUFFIXES = (".json", ".yaml", ".yml")


def seg(value: str) -> str:
    return quote(str(value), safe="")


def write(
    ctx: Context,
    verb: str,
    method: str,
    path: str,
    body: Any = None,
    *,
    preview: str | None = None,
) -> dict[str, Any]:
    """Run a mutating call, or (without ``--apply``) describe it and send nothing mutating.

    A dry-run may GET ``preview`` to show the current state; it never sends ``method``.
    """
    if ctx.apply:
        result = _api.call(lambda: ctx.client.request(method, path, body=body))
        return {"verb": verb, "applied": True, "dry_run": False, "result": result}
    out: dict[str, Any] = {
        "verb": verb,
        "applied": False,
        "dry_run": True,
        "would": {"method": method, "path": path, "body": body},
        "hint": "re-run with --apply to commit",
    }
    if preview:
        out["current"] = _api.call(lambda: ctx.client.request("GET", preview))
    return out


def sections_overview(noun: str, summary: str, ctx: Context, list_path: str) -> dict[str, Any]:
    """Descriptive overview of a noun: verbs plus a best-effort live count."""
    from culture_rules.cli.verbs import REGISTRY  # noqa: PLC0415

    verbs = [f"{v.name} — {v.summary}" for v in REGISTRY.verbs(noun)]
    state = "unavailable"
    try:
        items = ctx.client.request("GET", list_path)
        state = f"{len(items.get('items', []))} listed"
    except Exception as exc:  # noqa: BLE001 - overview never hard-fails
        state = f"unavailable ({exc.__class__.__name__})"
    return {
        "subject": f"culture-rules {noun}",
        "sections": [
            {"title": "What", "items": [summary]},
            {"title": "Live state", "items": [f"{noun}: {state}"]},
            {"title": "Verbs", "items": verbs},
            {
                "title": "Conventions",
                "items": [
                    "every verb supports --json",
                    "writes are dry-run unless --apply; a dry-run changes nothing",
                    "talks to the HTTP API only (CULTURE_RULES_API_URL, CULTURE_RULES_TOKEN)",
                ],
            },
        ],
    }


def collect_files(path: str | None, files: dict | None, noun: str) -> dict[str, str]:
    """``<noun>/<id>.<ext>`` -> text from a local directory/file, or the ``files`` param."""
    if files:
        found = {str(k): str(v) for k, v in files.items()}
    elif path:
        root = Path(path)
        if not root.exists():
            raise CliError(EXIT_USER_ERROR, f"no such path: {path}", "pass an exported directory")
        if root.is_file():
            doc = json.loads(root.read_text())
            found = {str(k): str(v) for k, v in (doc.get("files") or doc).items()}
        else:
            found = {
                p.relative_to(root).as_posix(): p.read_text()
                for p in sorted(root.rglob("*"))
                if p.is_file() and p.suffix in _SUFFIXES
            }
    else:
        raise CliError(EXIT_USER_ERROR, "import needs a path or files", "pass a directory")
    picked = {k: v for k, v in found.items() if k.startswith(f"{noun}/")}
    if not picked:
        raise CliError(
            EXIT_USER_ERROR,
            f"nothing to import: no {noun}/<id>.json files found",
            "export first with the noun's 'export' verb and import that directory",
        )
    return picked


def definition_verbs(noun: str, singular: str, summary: str, *, exchange: bool) -> list[Verb]:
    """The verbs every definition noun shares (the API serves the same routes for each)."""
    base = f"/{noun}"

    def overview(ctx: Context) -> dict[str, Any]:
        return sections_overview(noun, summary, ctx, base)

    def list_(ctx: Context, include_deleted: bool = False) -> Any:
        q = {"include_deleted": "true"} if include_deleted else None
        return _api.call(lambda: ctx.client.request("GET", base, query=q))

    def show(ctx: Context, id: str) -> Any:
        return _api.call(lambda: ctx.client.request("GET", f"{base}/{seg(id)}"))

    def create(ctx: Context, body: dict) -> Any:
        return write(ctx, f"{noun} create", "POST", base, body)

    def update(ctx: Context, id: str, body: dict) -> Any:
        p = f"{base}/{seg(id)}"
        return write(ctx, f"{noun} update", "PUT", p, body, preview=p)

    def action(name: str):
        def handler(ctx: Context, id: str) -> Any:
            p = f"{base}/{seg(id)}"
            if name == "delete":
                return write(ctx, f"{noun} delete", "DELETE", p, preview=p)
            return write(ctx, f"{noun} {name}", "POST", f"{p}/{name}", preview=p)

        return handler

    v = Verb
    verbs = [
        v(noun, "overview", f"Describe the {noun} noun and its verbs", overview),
        v(
            noun,
            "list",
            f"List {noun}",
            list_,
            (Param("include_deleted", "boolean", "include soft-deleted items"),),
        ),
        v(noun, "show", f"Show one {singular}", show, (ID,)),
        v(noun, "create", f"Create a {singular}", create, (BODY,), True, "editor"),
        v(noun, "update", f"Replace a {singular}", update, (ID, BODY), True, "editor"),
        v(noun, "enable", f"Enable a {singular}", action("enable"), (ID,), True, "editor"),
        v(noun, "disable", f"Disable a {singular}", action("disable"), (ID,), True, "editor"),
        v(
            noun,
            "delete",
            f"Soft-delete a {singular} (restorable)",
            action("delete"),
            (ID,),
            True,
            "editor",
        ),
        v(
            noun,
            "restore",
            f"Restore a soft-deleted {singular}",
            action("restore"),
            (ID,),
            True,
            "editor",
        ),
    ]

    def purge(ctx: Context, id: str) -> dict[str, Any]:
        # the server plans a purge itself (apply=false removes nothing), so a dry-run shows
        # whether the purge would succeed; admin-only either way
        result = _api.call(
            lambda: ctx.client.request("POST", f"{base}/{seg(id)}/purge", body={"apply": ctx.apply})
        )
        return {
            "verb": f"{noun} purge",
            "applied": bool(result.get("applied")),
            "dry_run": not ctx.apply,
            "result": result,
        }

    verbs.append(
        v(
            noun,
            "purge",
            f"Permanently remove a soft-deleted {singular} (admin)",
            purge,
            (ID,),
            True,
            "admin",
        )
    )
    if exchange:

        def export(ctx: Context, format: str = "json") -> Any:
            out = _api.call(lambda: ctx.client.request("GET", "/export", query={"format": format}))
            files = {k: t for k, t in out["files"].items() if k.startswith(f"{noun}/")}
            return {"format": out["format"], "files": files}

        def import_(
            ctx: Context, path: str | None = None, files: dict | None = None
        ) -> dict[str, Any]:
            picked = collect_files(path, files, noun)
            plan = _api.call(
                lambda: ctx.client.request(
                    "POST", "/import", body={"files": picked, "apply": ctx.apply}
                )
            )
            return {
                "verb": f"{noun} import",
                "applied": bool(plan.get("applied")),
                "dry_run": not ctx.apply,
                "would": {"method": "POST", "path": "/import", "files": sorted(picked)},
                "result": plan,
            }

        verbs += [
            v(
                noun,
                "export",
                f"Export {noun} as files",
                export,
                (Param("format", help="json or yaml", default="json"),),
            ),
            v(
                noun,
                "import",
                f"Import {noun} from an exported directory",
                import_,
                (
                    Param("path", help="directory (or bundle .json) to import", positional=True),
                    Param("files", "object", "relative path -> text, instead of a path"),
                ),
                True,
                "editor",
            ),
        ]
    return verbs
