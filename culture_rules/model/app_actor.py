"""Declaration shape for actors of kind ``app`` (an external-surface integration).

An ``app`` actor is the credentialed identity through which the engine receives events from,
and acts on, one outside surface (GitHub, Jira or Discord). Everything lives in
``Actor.params``; the webhook sink, the surface clients and the web Actors form read this::

    surface:       "github" | "jira" | "discord"
    events:        ["github.pr.opened", ...]   dotted event types the app declares it emits
    probes:        [{"name": str, "command": str, "schedule"?: str}, ...]   optional
    actions:       ["github.comment", ...]     action kinds (model.action_kinds) it can perform
    connection:    {...}                       per-surface config, see below (required)
    self_identity: str                         optional login of the App/bot/service account,
                                               used to tag self-authored events

``connection`` per surface (secret-looking keys hold ``grant:NAME`` references only; a
literal is refused on save with ``secret_literal`` by ``assert_refs_only``)::

    github:  {app_id, installation_id, private_key: "grant:N", webhook_secret: "grant:N",
              repos: [allow-list]}
    jira:    {site, email, token: "grant:N", webhook_token: "grant:N", projects: [...]}
    discord: {bot_token: "grant:N", guild_id, channels: [...]}

Event types follow the events-cli rule: lowercase dotted segments, at least two. Standard
library only; :func:`app_param_errors` returns ``(path, code, message)`` tuples relative to
``params`` and never raises.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from culture_rules.model.action_kinds import resolve_kind

__all__ = ["APP_SURFACES", "EVENT_TYPE_RE", "app_param_errors"]

APP_SURFACES: tuple[str, ...] = ("github", "jira", "discord")
EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")

Problem = tuple[str, str, str]


def _str_list(params: Mapping, key: str, check, out: list[Problem]) -> None:
    value = params.get(key)
    if value is None:
        return
    if not isinstance(value, (list, tuple)):
        out.append((key, "invalid_type", f"{key} must be a list"))
        return
    for i, item in enumerate(value):
        path = f"{key}[{i}]"
        if not isinstance(item, str):
            out.append((path, "invalid_type", f"{key} entries must be strings"))
        else:
            check(path, item, out)


def _check_event(path: str, item: str, out: list[Problem]) -> None:
    if not EVENT_TYPE_RE.match(item):
        out.append((path, "invalid_event_type", f"{item!r} is not a lowercase dotted event type"))


def _check_action(path: str, item: str, out: list[Problem]) -> None:
    if resolve_kind(item) is None:
        out.append((path, "unknown_action_kind", f"{item!r} is not a known action kind"))


def _check_probe_field(value: Any, path: str, name: str, out: list[Problem]) -> None:
    """A probe's name/command: missing or empty is ``required``, a non-string ``invalid_type``."""
    if value is None or value == "":
        out.append((path, "required", f"probe {name} is required"))
    elif not isinstance(value, str):
        out.append((path, "invalid_type", f"probe {name} must be a string"))


def _check_probes(params: Mapping, out: list[Problem]) -> None:
    probes = params.get("probes")
    if probes is None:
        return
    if not isinstance(probes, (list, tuple)):
        out.append(("probes", "invalid_type", "probes must be a list"))
        return
    for i, probe in enumerate(probes):
        base = f"probes[{i}]"
        if not isinstance(probe, Mapping):
            out.append((base, "invalid_type", "a probe must be an object"))
            continue
        for name in ("name", "command"):
            _check_probe_field(probe.get(name), f"{base}.{name}", name, out)
        schedule = probe.get("schedule")
        if schedule is not None and not isinstance(schedule, str):
            out.append((f"{base}.schedule", "invalid_type", "schedule must be a string"))


def app_param_errors(params: Any) -> list[Problem]:
    """Problems with an ``app`` actor's ``params``, as ``(path, code, message)`` tuples."""
    out: list[Problem] = []
    if not isinstance(params, Mapping):
        return [("", "invalid_type", "params must be an object")]
    surface = params.get("surface")
    if surface is None:
        out.append(("surface", "required", "surface is required"))
    elif surface not in APP_SURFACES:
        out.append(
            ("surface", "invalid_value", f"{surface!r} is not one of {', '.join(APP_SURFACES)}")
        )
    _str_list(params, "events", _check_event, out)
    _str_list(params, "actions", _check_action, out)
    _check_probes(params, out)
    connection = params.get("connection")
    if connection is None:
        out.append(("connection", "required", "connection is required"))
    elif not isinstance(connection, Mapping):
        out.append(("connection", "invalid_type", "connection must be an object"))
    identity = params.get("self_identity")
    if identity is not None and not isinstance(identity, str):
        out.append(("self_identity", "invalid_type", "self_identity must be a string"))
    return out
