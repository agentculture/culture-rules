"""The exchange bundle: everything that can be exported/imported as definition files."""

from __future__ import annotations

import re
from dataclasses import dataclass

from culture_rules.model.actor import Actor
from culture_rules.model.common import SCHEMA_VERSION, Model, doc
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Workflow

__all__ = ["KINDS", "NAME_RE", "SECRET_REF_RE", "Bundle", "SecretRef", "check_name"]

#: Safe file stem: no separators, no leading dot, so a definition id can never escape its dir.
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: A secret *reference* is ``scheme:locator`` (``env:GITHUB_TOKEN``, ``vault:kv/gh``); a bare
#: value (no scheme) is rejected so a real secret cannot leak through an export.
SECRET_REF_RE = re.compile(r"^[a-z][a-z0-9+.-]*:[^\s]+$")

#: kind -> (directory, model class); the directory is also the store collection name.
KINDS: dict[str, type] = {
    "rules": Rule,
    "workflows": Workflow,
    "actors": Actor,
}


def check_name(name: object) -> str:
    """Return ``name`` if it is a safe file stem, else raise ``ValueError``."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise ValueError(f"unsafe or empty definition id: {name!r}")
    return name


@dataclass(frozen=True, kw_only=True)
class SecretRef(Model):
    """A named pointer to a secret held elsewhere; never the secret's value."""

    name: str = doc("Secret name (file stem)")
    ref: str = doc("Reference such as env:NAME or vault:path; never the value")
    schema_version: str = doc("Document schema version", default=SCHEMA_VERSION)

    def check(self) -> None:
        """Raise ``ValueError`` unless ``name`` is a safe stem and ``ref`` a reference."""
        check_name(self.name)
        if not isinstance(self.ref, str) or not SECRET_REF_RE.match(self.ref):
            raise ValueError(
                f"secret {self.name!r}: ref must be a reference like 'env:NAME', not a value"
            )


@dataclass(frozen=True)
class Bundle:
    """Rules, workflows, actors and secret references, as parsed definitions."""

    rules: tuple[Rule, ...] = ()
    workflows: tuple[Workflow, ...] = ()
    actors: tuple[Actor, ...] = ()
    secrets: tuple[SecretRef, ...] = ()
