"""Declaration shape for actors of kind ``app`` (an external-surface integration).

An ``app`` actor is the credentialed identity through which the engine receives events from,
and acts on, one outside surface (GitHub, Jira, Discord, email or web search). Everything lives in
``Actor.params``; the webhook sink, the surface clients and the web Actors form read this::

    surface:       "github" | "jira" | "discord" | "email" | "search"
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
    discord: {bot_token: "grant:N", guild_id: one id or [ids] (unset = every guild),
              channels: [...], application_id (for slash and message commands),
              command_guild_id: one id or [ids] within guild_id (unset = all of them)}
    commands:      [{name, type: slash|message, ...}, ...]   discord only, optional: the
                   application commands it offers (model/discord_command_config.py)
    email:   {api_key: "grant:N", domain: one of EMAIL_DOMAINS (default culture.dev),
              local_part (default no-reply), recipients: [allow-list of addresses]}
    search:  {api_key: "grant:N"}

Only the ``email`` and ``search`` connections are checked field by field here (a literal
``api_key``, a domain outside :data:`EMAIL_DOMAINS`, a malformed local part or recipient are
refused with their path); the other surfaces' connections are checked by their clients.

Event types follow the events-cli rule: lowercase dotted segments, at least two. Standard
library only; :func:`app_param_errors` returns ``(path, code, message)`` tuples relative to
``params`` and never raises.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from culture_rules.actors.secrets import is_secret_ref
from culture_rules.model.action_kinds import resolve_kind
from culture_rules.model.discord_command_config import command_problems

__all__ = [
    "APP_SURFACES",
    "EMAIL_DEFAULT_DOMAIN",
    "EMAIL_DEFAULT_LOCAL_PART",
    "EMAIL_DOMAINS",
    "EVENT_TYPE_RE",
    "app_param_errors",
    "email_sender",
    "is_email_address",
]

APP_SURFACES: tuple[str, ...] = ("github", "jira", "discord", "email", "search")
#: The sender domains an ``email`` actor may send from (both verified with Resend).
EMAIL_DOMAINS: tuple[str, ...] = ("culture.dev", "jetson-ai-lab.org")
EMAIL_DEFAULT_DOMAIN = EMAIL_DOMAINS[0]
EMAIL_DEFAULT_LOCAL_PART = "no-reply"
# An RFC 5322 dot-atom local part (no quoting, no whitespace, no "@", no control characters)
# and a dotted host name: deliberately narrower than the RFC, wide enough for real mailboxes.
_LOCAL_PART_RE = re.compile(r"^(?!\.)(?!.*\.\.)[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]{1,64}(?<!\.)$")
_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_HOST_RE = re.compile(rf"^{_LABEL}(?:\.{_LABEL})+$")
_INVALID = "invalid_value"
_REQUIRED = "required"
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


def is_email_address(value: Any) -> bool:
    """Whether ``value`` is a plausible address: one ``@``, a dot-atom local part, a host."""
    if not isinstance(value, str) or value.count("@") != 1:
        return False
    local, host = value.split("@")
    return bool(_LOCAL_PART_RE.fullmatch(local)) and bool(_HOST_RE.fullmatch(host))


def email_sender(connection: Mapping) -> str:
    """The from address of an ``email`` connection: ``local_part@domain``, with defaults."""
    local = connection.get("local_part") or EMAIL_DEFAULT_LOCAL_PART
    domain = connection.get("domain") or EMAIL_DEFAULT_DOMAIN
    return f"{local}@{domain}"


def _check_api_key(conn: Mapping, out: list[Problem]) -> None:
    """``api_key`` is required and must be a ``grant:NAME`` reference (never echoed)."""
    value = conn.get("api_key")
    if value is None or value == "":
        out.append(("connection.api_key", _REQUIRED, "api_key is required"))
    elif not is_secret_ref(value):
        out.append(
            ("connection.api_key", "secret_literal", "api_key must be a 'grant:<NAME>' reference")
        )


def _check_recipients(conn: Mapping, out: list[Problem]) -> None:
    value = conn.get("recipients")
    path = "connection.recipients"
    # the allow-list may be empty (h18: the shipped actor sends to nobody until the operator
    # seeds it); the email port refuses every recipient then
    if value is None:
        out.append((path, _REQUIRED, "recipients (the allow-list) is required"))
    elif not isinstance(value, (list, tuple)):
        out.append((path, "invalid_type", "recipients must be a list of addresses"))
    else:
        for i, item in enumerate(value):
            if not is_email_address(item):
                out.append((f"{path}[{i}]", _INVALID, "not a valid email address"))


def _check_email_connection(conn: Mapping, out: list[Problem]) -> None:
    _check_api_key(conn, out)
    domain = conn.get("domain")
    if domain is not None and domain not in EMAIL_DOMAINS:
        out.append(
            ("connection.domain", _INVALID, f"domain must be one of {', '.join(EMAIL_DOMAINS)}")
        )
    local = conn.get("local_part")
    if local is not None and not (isinstance(local, str) and _LOCAL_PART_RE.fullmatch(local)):
        out.append(
            ("connection.local_part", _INVALID, "local_part is not a valid email local part")
        )
    _check_recipients(conn, out)


_CONNECTION_CHECKS = {
    "email": _check_email_connection,
    "search": _check_api_key,
}


def _check_connection(surface: Any, connection: Any, out: list[Problem]) -> None:
    if connection is None:
        out.append(("connection", "required", "connection is required"))
    elif not isinstance(connection, Mapping):
        out.append(("connection", "invalid_type", "connection must be an object"))
    elif isinstance(surface, str) and surface in _CONNECTION_CHECKS:
        _CONNECTION_CHECKS[surface](connection, out)


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
    if "commands" in params and surface != "discord":
        out.append(("commands", "not_allowed", "commands are for a discord app actor"))
    elif surface == "discord":
        out.extend(command_problems(params.get("commands")))
    _check_connection(surface, params.get("connection"), out)
    identity = params.get("self_identity")
    if identity is not None and not isinstance(identity, str):
        out.append(("self_identity", "invalid_type", "self_identity must be a string"))
    return out
