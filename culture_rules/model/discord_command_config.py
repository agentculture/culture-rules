"""A Discord ``app`` actor's application commands, as configuration (discord-surfaces d12).

Which commands a bot offers is a flow's choice, so it lives on the actor, not in code::

    params.commands: [
      {"name": "save", "type": "slash", "description": "Save a message to the library",
       "options": [{"name": "message", "description": "A message link or id",
                    "kind": "message", "required": false}],
       "not_here": "..."},                       # optional
      {"name": "Save to library", "type": "message"},
    ]

* ``type`` is ``slash`` (a ``/name`` command) or ``message`` (a message's Apps menu entry,
  which targets the right-clicked message);
* a ``slash`` name matches :data:`SLASH_NAME_RE`; a ``message`` name is 1-32 characters;
  names are unique per type; at most :data:`MAX_COMMANDS` commands, at most
  :data:`MAX_MESSAGE_COMMANDS` of type ``message``;
* a ``slash`` command needs a ``description`` (1-100 characters) and may take at most
  :data:`MAX_OPTIONS` ``options``; a ``message`` command takes neither;
* an option has a ``name`` (:data:`OPTION_NAME_RE`, not ``interaction``, unique in its
  command), a ``description`` (1-100), a ``kind`` (``string``, the default, or ``message``: a
  message link or id, read into the event's target; at most one per command) and
  ``required`` (a boolean, default false; required options come first);
* ``not_here`` (1-200 characters) is the private answer when no rule takes the command
  there; unset, :data:`DEFAULT_NOT_HERE`.

:func:`definitions` turns the list into Discord's API shape (what is registered, and what
the sync hash covers). Standard library only.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "COMMAND_TYPES",
    "DEFAULT_NOT_HERE",
    "MAX_COMMANDS",
    "MAX_MESSAGE_COMMANDS",
    "MAX_OPTIONS",
    "OPTION_KINDS",
    "OPTION_NAME_RE",
    "SLASH_NAME_RE",
    "command_problems",
    "definitions",
    "not_here",
]

COMMAND_TYPES = ("slash", "message")
OPTION_KINDS = ("string", "message")
SLASH_NAME_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
OPTION_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]{0,31}$")
"""An option's name is also the callback's keyword, so it is a Python identifier."""
MAX_COMMANDS = 10
MAX_MESSAGE_COMMANDS = 5  # Discord's own limit per guild
MAX_OPTIONS = 5
_RESERVED_OPTION_NAMES = frozenset({"interaction"})
_DESCRIPTION_MAX = 100
_NAME_MAX = 32
_NOT_HERE_MAX = 200

DEFAULT_NOT_HERE = "This command is not set up here."

_SLASH, _MESSAGE = 1, 3  # Discord's application command types
_STRING_OPTION = 3

Problem = tuple[str, str, str]
_INVALID = "invalid_value"
_TYPE = "invalid_type"
_REQUIRED = "required"


def _text_ok(value: Any, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value.strip()) <= limit and len(value) <= limit


def _check_name(cmd: Mapping[str, Any], base: str, out: list[Problem]) -> None:
    name = cmd.get("name")
    if cmd.get("type") == "slash":
        if not isinstance(name, str) or not SLASH_NAME_RE.match(name):
            out.append((f"{base}.name", _INVALID, "a slash command's name is 1-32 of a-z 0-9 _ -"))
    elif not _text_ok(name, _NAME_MAX):
        out.append((f"{base}.name", _INVALID, "a message command's name is 1-32 characters"))


def _check_option(opt: Any, path: str, seen: set[str], out: list[Problem]) -> None:
    if not isinstance(opt, Mapping):
        out.append((path, _TYPE, "an option is an object"))
        return
    name = opt.get("name")
    if (
        not isinstance(name, str)
        or not OPTION_NAME_RE.match(name)
        or name in _RESERVED_OPTION_NAMES
    ):
        out.append(
            (f"{path}.name", _INVALID, "an option's name is a-z 0-9 _, not starting with a digit")
        )
    elif name in seen:
        out.append((f"{path}.name", "duplicate", f"option {name!r} is named twice"))
    else:
        seen.add(name)
    if not _text_ok(opt.get("description"), _DESCRIPTION_MAX):
        out.append(
            (f"{path}.description", _REQUIRED, "an option needs a 1-100 character description")
        )
    if opt.get("kind", "string") not in OPTION_KINDS:
        out.append((f"{path}.kind", _INVALID, "an option's kind is string or message"))
    if not isinstance(opt.get("required", False), bool):
        out.append((f"{path}.required", _TYPE, "required is true or false"))


def _check_options(options: Any, base: str, out: list[Problem]) -> None:
    path = f"{base}.options"
    if not isinstance(options, Sequence) or isinstance(options, (str, bytes)):
        out.append((path, _TYPE, "options must be a list"))
        return
    if len(options) > MAX_OPTIONS:
        out.append((path, "too_many", f"a command takes at most {MAX_OPTIONS} options"))
    seen: set[str] = set()
    optional_seen = message_seen = False
    for j, opt in enumerate(options):
        here = f"{path}[{j}]"
        _check_option(opt, here, seen, out)
        if not isinstance(opt, Mapping):
            continue
        if opt.get("kind") == "message":
            if message_seen:
                out.append((f"{here}.kind", _INVALID, "at most one message option per command"))
            message_seen = True
        if opt.get("required") is True and optional_seen:
            out.append((f"{here}.required", _INVALID, "required options come before optional ones"))
        optional_seen = optional_seen or opt.get("required") is not True


def _check_command(cmd: Any, base: str, out: list[Problem]) -> None:
    if not isinstance(cmd, Mapping):
        out.append((base, _TYPE, "a command is an object"))
        return
    kind = cmd.get("type")
    if kind not in COMMAND_TYPES:
        out.append((f"{base}.type", _INVALID, "a command's type is slash or message"))
        return
    _check_name(cmd, base, out)
    if kind == "slash":
        if not _text_ok(cmd.get("description"), _DESCRIPTION_MAX):
            out.append(
                (
                    f"{base}.description",
                    _REQUIRED,
                    "a slash command needs a 1-100 character description",
                )
            )
        if cmd.get("options") is not None:
            _check_options(cmd["options"], base, out)
    else:
        for key in ("description", "options"):
            if key in cmd:
                out.append((f"{base}.{key}", "not_allowed", f"a message command takes no {key}"))
    if "not_here" in cmd and not _text_ok(cmd["not_here"], _NOT_HERE_MAX):
        out.append((f"{base}.not_here", _INVALID, "not_here is 1-200 characters"))


def command_problems(commands: Any) -> list[Problem]:
    """Problems with ``params.commands`` as ``(path, code, message)``; none when absent."""
    if commands is None:
        return []
    if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)):
        return [("commands", _TYPE, "commands must be a list")]
    out: list[Problem] = []
    if len(commands) > MAX_COMMANDS:
        out.append(("commands", "too_many", f"at most {MAX_COMMANDS} commands"))
    menus = [c for c in commands if isinstance(c, Mapping) and c.get("type") == "message"]
    if len(menus) > MAX_MESSAGE_COMMANDS:
        out.append(("commands", "too_many", f"at most {MAX_MESSAGE_COMMANDS} message commands"))
    seen: set[tuple[Any, Any]] = set()
    for i, cmd in enumerate(commands):
        base = f"commands[{i}]"
        before = len(out)
        _check_command(cmd, base, out)
        if len(out) > before or not isinstance(cmd, Mapping):
            continue
        key = (cmd.get("type"), cmd.get("name"))
        if key in seen:
            out.append((f"{base}.name", "duplicate", f"{cmd.get('name')!r} is named twice"))
        seen.add(key)
    return out


def _option_definition(opt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": _STRING_OPTION,  # a message link or id is text too
        "name": opt["name"],
        "description": opt["description"].strip(),
        "required": opt.get("required") is True,
    }


def definitions(commands: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    """Valid ``params.commands`` in Discord's API shape, in their order."""
    out: list[dict[str, Any]] = []
    for cmd in commands:
        if cmd["type"] == "message":
            out.append({"type": _MESSAGE, "name": cmd["name"].strip()})
            continue
        item: dict[str, Any] = {
            "type": _SLASH,
            "name": cmd["name"],
            "description": cmd["description"].strip(),
        }
        if cmd.get("options"):
            item["options"] = [_option_definition(o) for o in cmd["options"]]
        out.append(item)
    return tuple(out)


def not_here(command: Mapping[str, Any]) -> str:
    """The private answer when no rule takes ``command`` where it was used."""
    text = command.get("not_here")
    return text.strip() if isinstance(text, str) and text.strip() else DEFAULT_NOT_HERE
