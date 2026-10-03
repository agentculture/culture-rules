"""Generate one JSON Schema (draft 2020-12) per model from the dataclasses.

The committed ``schemas/`` directory is the pinned contract for io, server, web
and CLI; a test asserts it matches this generator. Regenerate with::

    uv run python -m culture_rules.model.schema --write schemas
    uv run python -m culture_rules.model.schema --check schemas
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from culture_rules.model import serde
from culture_rules.model.action import Action
from culture_rules.model.actor import Actor
from culture_rules.model.common import SCHEMA_VERSION
from culture_rules.model.machine import Machine
from culture_rules.model.placement import Placement
from culture_rules.model.rule import Rule
from culture_rules.model.workflow import Step, Workflow

__all__ = ["MODELS", "SCHEMAS_DIR", "generate_all", "json_schema", "main", "schema_filename"]

DRAFT = "https://json-schema.org/draft/2020-12/schema"

#: The models that get a top-level schema file each.
MODELS: tuple[type, ...] = (Rule, Workflow, Step, Action, Actor, Machine, Placement)


def schema_filename(cls: type) -> str:
    return f"{cls.__name__.lower()}.schema.json"


class _Builder:
    def __init__(self, root: type) -> None:
        self.root = root
        self.defs: dict[str, dict[str, Any]] = {}

    def ref(self, cls: type) -> dict[str, Any]:
        if cls is self.root:
            return {"$ref": "#"}
        if cls.__name__ not in self.defs:
            self.defs[cls.__name__] = {}  # placeholder breaks recursion
            self.defs[cls.__name__] = self.object_schema(cls)
        return {"$ref": f"#/$defs/{cls.__name__}"}

    def type_schema(self, tp: Any) -> dict[str, Any]:
        if tp is Any:
            return {}
        if serde.is_optional(tp):
            return {"anyOf": [self.type_schema(serde.strip_optional(tp)), {"type": "null"}]}
        origin = get_origin(tp)
        if origin is Literal:
            return {"type": "string", "enum": list(get_args(tp))}
        if origin is tuple:
            return {"type": "array", "items": self.type_schema(get_args(tp)[0])}
        if origin is dict:
            item = get_args(tp)[1]
            if item is Any:
                return {"type": "object"}
            return {"type": "object", "additionalProperties": self.type_schema(item)}
        if dataclasses.is_dataclass(tp):
            return self.ref(tp)
        return {
            str: {"type": "string"},
            int: {"type": "integer"},
            float: {"type": "number"},
            bool: {"type": "boolean"},
        }[tp]

    def object_schema(self, cls: type) -> dict[str, Any]:
        hints = serde.field_types(cls)
        props: dict[str, Any] = {}
        required: list[str] = []
        for f in dataclasses.fields(cls):
            prop = dict(self.type_schema(hints[f.name]))
            if "doc" in f.metadata:
                prop["description"] = f.metadata["doc"]
            if f.default is not dataclasses.MISSING:
                prop["default"] = serde.to_plain(f.default)
            elif f.default_factory is not dataclasses.MISSING:
                prop["default"] = serde.to_plain(f.default_factory())
            else:
                required.append(f.name)
            props[f.name] = prop
        schema: dict[str, Any] = {"type": "object"}
        doc = (cls.__doc__ or "").strip().splitlines()
        if doc:
            schema["description"] = doc[0]
        schema["properties"] = props
        schema["required"] = required
        schema["additionalProperties"] = False
        schema.update(getattr(cls, "__schema_extra__", {}))
        return schema


def json_schema(cls: type) -> dict[str, Any]:
    """The full JSON Schema document for model ``cls``."""
    builder = _Builder(cls)
    body = builder.object_schema(cls)
    out: dict[str, Any] = {
        "$schema": DRAFT,
        "$id": schema_filename(cls),
        "title": cls.__name__,
        "x-schema-version": SCHEMA_VERSION,
    }
    out.update(body)
    if builder.defs:
        out["$defs"] = dict(sorted(builder.defs.items()))
    return out


def generate_all() -> dict[str, str]:
    """``{filename: text}`` for every model, exactly as committed under ``schemas/``."""
    return {
        schema_filename(cls): json.dumps(json_schema(cls), indent=2, ensure_ascii=False) + "\n"
        for cls in MODELS
    }


#: The committed schema directory: the only place ``main`` reads from or writes to.
SCHEMAS_DIR = Path(__file__).resolve().parents[2] / "schemas"


def main(argv: list[str] | None = None, *, schemas_dir: Path | None = None) -> int:
    """``--write`` / ``--check`` the generated schemas against the repo's ``schemas/``.

    The optional ``DIR`` argument exists for readability (``--check schemas``) only: it must
    resolve to the repository's ``schemas/`` directory, and anything else - a ``../`` escape,
    an absolute path elsewhere - is refused before any file is touched. The directory actually
    read or written is never taken from the command line; ``schemas_dir`` lets a test point
    the generator at a scratch directory from code.
    """
    parser = argparse.ArgumentParser(
        prog="python -m culture_rules.model.schema",
        description="Write or check the generated model JSON Schemas in the repo's schemas/.",
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--write", nargs="?", const="", metavar="DIR", help="write schemas into schemas/"
    )
    mode.add_argument(
        "--check", nargs="?", const="", metavar="DIR", help="exit 1 if schemas/ is stale"
    )
    args = parser.parse_args(argv)
    named = args.write if args.write is not None else args.check
    if named and Path(named).resolve() != SCHEMAS_DIR:
        parser.error(
            f"DIR must be the repository's schemas/ directory ({SCHEMAS_DIR}), got {named!r}"
        )
    target = SCHEMAS_DIR if schemas_dir is None else schemas_dir
    generated = generate_all()
    if args.write is not None:
        target.mkdir(parents=True, exist_ok=True)
        for name, text in generated.items():
            (target / name).write_text(text, encoding="utf-8")
        print(f"wrote {len(generated)} schemas to {target}")
        return 0
    stale = sorted(
        name
        for name, text in generated.items()
        if not (target / name).is_file() or (target / name).read_text(encoding="utf-8") != text
    )
    extra = sorted(p.name for p in target.glob("*.schema.json") if p.name not in generated)
    for name in stale:
        print(f"stale: {name}", file=sys.stderr)
    for name in extra:
        print(f"unexpected: {name}", file=sys.stderr)
    return 1 if stale or extra else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
