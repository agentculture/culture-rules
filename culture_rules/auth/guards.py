"""Body-dependent authorization on definition saves (the route matrix cannot see bodies).

- A workflow step carrying inline script text (on the step or in its ``config``, at any
  nesting depth) may be saved only by an admin
  (:func:`culture_rules.actors.code.check_step_inline_allowed`).
- An actor definition must hold secret *references* (``grant:<NAME>``), never literals
  (:func:`culture_rules.actors.secrets.assert_refs_only`).

Applied to create, update and import alike. Standard-library only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from culture_rules.actors.code import check_step_inline_allowed
from culture_rules.actors.secrets import assert_refs_only
from culture_rules.auth.principal import Principal
from culture_rules.io import exchange

__all__ = ["check_actor_secrets", "check_definition", "check_import", "check_workflow_inline"]


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
