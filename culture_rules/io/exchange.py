"""Export and import of definitions as files: ``rules/<id>.yaml``, ``workflows/<id>.yaml`` ...

Both directions are *writes* and so follow the CLI contract: they compute a plan with a
diff and change nothing unless ``apply=True``. Imports are strict (unknown fields are
reported as errors in the plan, never raised) and all-or-nothing: a plan with any error is
never applied. Secrets travel as references only (``env:NAME``), never values.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from culture_rules.io import codec
from culture_rules.io.bundle import KINDS, Bundle, SecretRef, check_name
from culture_rules.model import serde
from culture_rules.model.validate import validate
from culture_rules.store.port import StoreOps

__all__ = [
    "Change",
    "ExportPlan",
    "ImportPlan",
    "IssueRecord",
    "ReadResult",
    "bundle_files",
    "bundle_from_store",
    "export_bundle",
    "import_bundle",
    "read_bundle",
    "read_files",
]

SECRETS_DIR = "secrets"
_ENVELOPE = ("updated_at",)  # store-owned fields that are not part of a definition
Action = Literal["add", "change", "unchanged"]


@dataclass(frozen=True)
class IssueRecord:
    """One problem found while reading definition files."""

    path: str  # "<file>" or "<file>:<dotted.field>"
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class Change:
    """One planned file write (export) or store write (import)."""

    kind: str
    id: str
    path: str
    action: Action
    diff: str = ""


@dataclass(frozen=True)
class ReadResult:
    bundle: Bundle
    errors: list[IssueRecord]


@dataclass
class ExportPlan:
    directory: Path
    changes: list[Change]
    applied: bool = False

    def render(self) -> str:
        return _render(self.changes, "export", self.applied)


@dataclass
class ImportPlan:
    changes: list[Change]
    errors: list[IssueRecord] = field(default_factory=list)
    applied: bool = False

    def render(self) -> str:
        out = _render(self.changes, "import", self.applied)
        errs = "".join(f"\nerror: {e.path}: {e.code}: {e.message}" for e in self.errors)
        return out + errs


def _render(changes: list[Change], verb: str, applied: bool) -> str:
    head = f"{verb} ({'applied' if applied else 'dry-run'}):"
    lines = [head]
    for c in changes:
        lines.append(f"{c.action:9} {c.path}")
        if c.diff:
            lines.append(c.diff.rstrip("\n"))
    return "\n".join(lines)


# --- bundle <-> files -------------------------------------------------------


def _docs(bundle: Bundle) -> list[tuple[str, str, dict[str, Any]]]:
    """(kind, id, plain dict) for everything in the bundle; validates names and refs."""
    out: list[tuple[str, str, dict[str, Any]]] = []
    for kind in KINDS:
        for obj in getattr(bundle, kind):
            out.append((kind, check_name(obj.id), obj.to_dict()))
    for secret in bundle.secrets:
        secret.check()
        out.append((SECRETS_DIR, secret.name, secret.to_dict()))
    return out


def bundle_files(bundle: Bundle, fmt: str = "yaml") -> dict[str, str]:
    """Relative path -> file text for a bundle. Raises ``ValueError`` on an unsafe id/secret."""
    ext = codec.EXTENSIONS[fmt][0] if fmt in codec.EXTENSIONS else None
    if ext is None:
        raise ValueError(f"unknown format {fmt!r} (expected 'yaml' or 'json')")
    return {f"{kind}/{ident}{ext}": codec.dumps(data, fmt) for kind, ident, data in _docs(bundle)}


def _diff(old: str, new: str, path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
        )
    )


def export_bundle(
    bundle: Bundle, directory: str | Path, *, fmt: str = "yaml", apply: bool = False
) -> ExportPlan:
    """Plan (and with ``apply=True`` perform) writing a bundle under ``directory``.

    Existing files for ids no longer in the bundle are left untouched.
    """
    root = Path(directory)
    files = bundle_files(bundle, fmt)
    changes: list[Change] = []
    for rel, text in sorted(files.items()):
        kind, _, name = rel.partition("/")
        target = root / rel
        old = target.read_text(encoding="utf-8") if target.is_file() else None
        if old is None:
            changes.append(Change(kind, name.rsplit(".", 1)[0], rel, "add", _diff("", text, rel)))
        elif old == text:
            changes.append(Change(kind, name.rsplit(".", 1)[0], rel, "unchanged"))
        else:
            changes.append(
                Change(kind, name.rsplit(".", 1)[0], rel, "change", _diff(old, text, rel))
            )
    plan = ExportPlan(root, changes)
    if apply:
        for c in changes:
            if c.action != "unchanged":
                target = root / c.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(files[c.path], encoding="utf-8")
        plan.applied = True
    return plan


def read_files(files: Mapping[str, str]) -> ReadResult:
    """Parse ``{relative path: text}`` strictly; every problem becomes an :class:`IssueRecord`."""
    errors: list[IssueRecord] = []
    found: dict[str, list[Any]] = {k: [] for k in (*KINDS, SECRETS_DIR)}
    seen: set[tuple[str, str]] = set()
    for rel in sorted(files):
        kind, _, fname = rel.partition("/")
        fmt = codec.format_of(fname)
        if kind not in found or fmt is None or "/" in fname:
            errors.append(
                IssueRecord(
                    rel,
                    "unrecognised_path",
                    "expected <rules|workflows|actors|secrets>/<id>.<yaml|yml|json>",
                )
            )
            continue
        cls = KINDS.get(kind, SecretRef)
        stem = fname.rsplit(".", 1)[0]
        try:
            data = codec.loads(files[rel], fmt)
            obj = serde.from_dict(cls, data)
        except serde.ModelParseError as exc:
            errors.append(
                IssueRecord(f"{rel}:{exc.path}" if exc.path else rel, exc.code, exc.message)
            )
            continue
        except ValueError as exc:
            errors.append(IssueRecord(rel, "parse", str(exc)))
            continue
        ident = obj.name if kind == SECRETS_DIR else obj.id
        if ident != stem:
            errors.append(
                IssueRecord(rel, "id_mismatch", f"file name {stem!r} does not match id {ident!r}")
            )
            continue
        if (kind, ident) in seen:
            errors.append(
                IssueRecord(rel, "duplicate_id", f"{kind}/{ident} is defined by more than one file")
            )
            continue
        seen.add((kind, ident))
        issues = _validate_one(obj, kind)
        errors.extend(IssueRecord(f"{rel}:{p}" if p else rel, c, m) for p, c, m in issues)
        found[kind].append(obj)
    bundle = Bundle(
        rules=tuple(found["rules"]),
        workflows=tuple(found["workflows"]),
        actors=tuple(found["actors"]),
        secrets=tuple(found[SECRETS_DIR]),
    )
    return ReadResult(bundle, errors)


def _validate_one(obj: Any, kind: str) -> list[tuple[str, str, str]]:
    if kind == SECRETS_DIR:
        try:
            obj.check()
        except ValueError as exc:
            return [("ref", "secret_value", str(exc))]
        return []
    return [(e.path, e.code, e.message) for e in validate(obj)]


def read_bundle(directory: str | Path) -> ReadResult:
    """Read every definition file under ``directory`` (see :func:`read_files`)."""
    root = Path(directory)
    files: dict[str, str] = {}
    for kind in (*KINDS, SECRETS_DIR):
        sub = root / kind
        if sub.is_dir():
            for p in sorted(sub.iterdir()):
                if p.is_file():
                    files[f"{kind}/{p.name}"] = p.read_text(encoding="utf-8")
    return read_files(files)


# --- store <-> bundle -------------------------------------------------------


def _strip(document: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in document.items() if k not in _ENVELOPE}


def bundle_from_store(store: StoreOps) -> Bundle:
    """Read all definitions out of a store (tolerant of unknown stored fields)."""
    parts: dict[str, tuple[Any, ...]] = {}
    for kind, cls in KINDS.items():
        docs = store.find(kind)
        parts[kind] = tuple(cls.from_dict(_strip(d), strict=False) for d in docs)  # type: ignore
    secrets = tuple(
        SecretRef.from_dict({"name": d["id"], "ref": d.get("ref"), **_schema(d)}, strict=False)
        for d in store.find(SECRETS_DIR)
    )
    return Bundle(secrets=secrets, **parts)


def _schema(document: Mapping[str, Any]) -> dict[str, Any]:
    return {"schema_version": document["schema_version"]} if "schema_version" in document else {}


def _stored_form(kind: str, obj: Any) -> dict[str, Any]:
    if kind == SECRETS_DIR:
        data = obj.to_dict()
        data["id"] = data.pop("name")
        return data
    return obj.to_dict()


def import_bundle(directory: str | Path, store: StoreOps, *, apply: bool = False) -> ImportPlan:
    """Plan (and with ``apply=True``, perform) importing ``directory`` into ``store``.

    The plan carries a per-document diff against what the store holds now. With any error in
    the plan, nothing is applied (``plan.applied`` stays False).
    """
    read = read_bundle(directory)
    return _plan_import(read, store, apply)


def _plan_import(read: ReadResult, store: StoreOps, apply: bool) -> ImportPlan:
    changes: list[Change] = []
    writes: list[tuple[str, dict[str, Any]]] = []
    for kind in (*KINDS, SECRETS_DIR):
        for obj in getattr(read.bundle, kind):
            new = _stored_form(kind, obj)
            ident = new["id"] if "id" in new else obj.id
            current = store.get(kind, ident)
            path = f"{kind}/{ident}"
            new_text = _pretty(new)
            if current is None:
                changes.append(Change(kind, ident, path, "add", _diff("", new_text, path)))
            else:
                old_text = _pretty(_normalise(kind, current))
                if old_text == new_text:
                    changes.append(Change(kind, ident, path, "unchanged"))
                    continue
                changes.append(Change(kind, ident, path, "change", _diff(old_text, new_text, path)))
            writes.append((kind, new))
    plan = ImportPlan(changes, list(read.errors))
    if apply and not plan.errors:
        _write_all(store, writes)
        plan.applied = True
    return plan


def _normalise(kind: str, stored: Mapping[str, Any]) -> dict[str, Any]:
    """The stored document as the definition form this version would write (tolerant read)."""
    data = _strip(stored)
    if kind == SECRETS_DIR:
        obj = SecretRef.from_dict(
            {"name": data["id"], "ref": data.get("ref"), **_schema(data)}, strict=False
        )
        return _stored_form(kind, obj)
    return KINDS[kind].from_dict(data, strict=False).to_dict()  # type: ignore[attr-defined]


def _pretty(data: Mapping[str, Any]) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def _write_all(store: Any, writes: list[tuple[str, dict[str, Any]]]) -> None:
    transaction = getattr(store, "transaction", None)
    if transaction is None:
        for kind, doc in writes:
            store.put(kind, doc)
        return
    with transaction() as tx:
        for kind, doc in writes:
            tx.put(kind, doc)
