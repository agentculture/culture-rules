"""Body-dependent authorization on definition saves (the route matrix cannot see bodies).

- A workflow step carrying inline script text (on the step or in its ``config``, at any
  nesting depth) may be saved only by an admin
  (:func:`culture_rules.actors.code.check_step_inline_allowed`).
- An actor definition must hold secret *references* (``grant:<NAME>``), never literals
  (:func:`culture_rules.actors.secrets.assert_refs_only`).
- A runner actor's command registry (``params.commands``) is code the runner host executes,
  so only an admin may add or change it (:func:`check_runner_commands`): any difference from
  the stored registry, a new runner with commands, and turning an actor that holds commands
  into a runner all count. An editor may still save a runner whose registry is unchanged.
  This check compares against the stored version, so it runs inside the save transaction
  (:func:`save_check`, passed to :class:`culture_rules.server.service.Definitions`).

Applied to create, update and import alike. Standard-library only.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from culture_rules.actors.code import InlineScriptDenied, check_step_inline_allowed
from culture_rules.actors.secrets import assert_refs_only
from culture_rules.auth.principal import Principal
from culture_rules.io import exchange

__all__ = [
    "RunnerCommandsDenied",
    "check_actor_secrets",
    "check_definition",
    "check_import",
    "check_runner_commands",
    "check_workflow_inline",
    "runner_commands",
    "save_check",
]

#: ``(kind, stored document or None, document about to be written)``; raises to refuse.
_SaveCheck = Callable[[str, "Mapping[str, Any] | None", Mapping[str, Any]], None]


class RunnerCommandsDenied(InlineScriptDenied):
    """A non-admin tried to add or change a runner actor's command registry (HTTP 403).

    A subclass of :class:`InlineScriptDenied`: registered commands are host code just like
    inline scripts, and the API answers both with the same 403 envelope.
    """

    code = "runner_commands_admin_only"

    def __init__(self, identity: str, actor_id: str) -> None:
        PermissionError.__init__(
            self,
            f"changing runner commands (actors/{actor_id} params.commands) is admin-only; "
            f"{identity!r} is not an admin",
        )
        self.identity = identity
        self.actor_id = actor_id


def _steps(steps: Any) -> Iterable[Mapping[str, Any]]:
    for step in steps if isinstance(steps, list | tuple) else ():
        if isinstance(step, Mapping):
            yield step
            yield from _steps(step.get("body"))


def check_workflow_inline(principal: Principal, body: Any) -> None:
    """Raise ``InlineScriptDenied`` if a non-admin saves a step with inline script text."""
    if not isinstance(body, Mapping):
        return

    def is_admin(_identity: str) -> bool:
        return principal.has_role("admin")

    for step in _steps(body.get("steps")):
        check_step_inline_allowed(principal.identity, step, is_admin=is_admin)
        config = step.get("config")
        if isinstance(config, Mapping):
            check_step_inline_allowed(principal.identity, config, is_admin=is_admin)


def check_actor_secrets(body: Any) -> None:
    """Raise ``SecretError`` if a secret-looking key nested in an actor holds a literal."""
    if isinstance(body, Mapping):
        assert_refs_only({k: v for k, v in body.items() if isinstance(v, Mapping | list)})


def runner_commands(doc: Any) -> Any:
    """The command registry a stored or incoming actor gives a runner (``{}`` when none).

    Normalised through JSON so a tuple and a list compare equal. A non-runner's
    ``params.commands`` is inert, so it counts as no registry: turning that actor into a
    runner is then a registry change.
    """
    if not isinstance(doc, Mapping) or doc.get("kind") != "runner":
        return {}
    params = doc.get("params")
    commands = params.get("commands") if isinstance(params, Mapping) else None
    return json.loads(json.dumps(commands or {}, sort_keys=True, default=str))


def check_runner_commands(
    principal: Principal, before: Mapping[str, Any] | None, after: Mapping[str, Any]
) -> None:
    """Raise :class:`RunnerCommandsDenied` if a non-admin changes a runner's registry."""
    if principal.has_role("admin"):
        return
    if runner_commands(after) != runner_commands(before):
        raise RunnerCommandsDenied(principal.identity, str(after.get("id", "?")))


def save_check(principal: Principal) -> _SaveCheck:
    """The in-transaction save check for ``principal`` (see ``service.SaveCheck``)."""

    def check(kind: str, before: Mapping[str, Any] | None, after: Mapping[str, Any]) -> None:
        if kind == "actors":
            check_runner_commands(principal, before, after)

    return check


def check_definition(principal: Principal, kind: str, body: Any) -> None:
    if kind == "workflows":
        check_workflow_inline(principal, body)
    elif kind == "actors":
        check_actor_secrets(body)


def check_import(principal: Principal, files: Mapping[str, str]) -> None:
    """Apply the save guards to every workflow and actor in an import request."""
    bundle = exchange.read_files(files).bundle
    for workflow in bundle.workflows:
        check_workflow_inline(principal, workflow.to_dict())
    for actor in bundle.actors:
        check_actor_secrets(actor.to_dict())
