"""Definition verbs behind the HTTP API (standard-library only; no web framework here).

Every save path - create, update and import - validates the definition with the model
validators and, for rules, runs :func:`culture_rules.engine.ruleset.validate_rule_set` over the
whole resulting rule set, refusing with :class:`Invalid` (HTTP 422) on any error. Every
mutating verb writes its change and exactly one audit entry in one transaction. Nothing is
cached in the process: each call reads the store.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from culture_rules.engine.audit import AuditLog, mutating_verb, require_identity
from culture_rules.engine.ruleset import validate_rule_set
from culture_rules.io import exchange
from culture_rules.io.bundle import KINDS, Bundle, SecretRef, check_name
from culture_rules.model.machine import Machine
from culture_rules.model.rule import Rule
from culture_rules.model.validate import validate, validate_data
from culture_rules.model.workflow import Workflow
from culture_rules.store.port import Document, StoragePort

__all__ = [
    "DEFINITION_KINDS",
    "Conflict",
    "Definitions",
    "Invalid",
    "NotFound",
    "ServiceError",
]

#: kind -> model class; the kind is also the store collection.
DEFINITION_KINDS: dict[str, type] = {**KINDS, "machines": Machine}
SECRETS = "secrets"


class ServiceError(Exception):
    """Base class: ``code`` is a stable machine-readable string, ``errors`` the details."""

    code = "error"

    def __init__(self, message: str, errors: Iterable[Mapping[str, Any]] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.errors = [dict(e) for e in errors]


class Invalid(ServiceError):
    """The input failed validation (HTTP 422)."""

    code = "invalid"


class NotFound(ServiceError):
    """The target does not exist (HTTP 404)."""

    code = "not_found"


class Conflict(ServiceError):
    """The target is in the wrong state for this verb (HTTP 409)."""

    code = "conflict"


def _ident(kind: str, obj: Any) -> str:
    return obj.name if kind == "machines" else obj.id


def _is_live(doc: Mapping[str, Any]) -> bool:
    return not doc.get("deleted_at")


def _tolerant(cls: type, doc: Mapping[str, Any]) -> Any | None:
    """Parse a stored document, ignoring unknown fields; ``None`` if unparsable or invalid.

    Used to build the surrounding rule set: a malformed stored document must not crash (or
    block) the save of an unrelated, valid one.
    """
    data = {
        k: v for k, v in doc.items() if k != "updated_at" and not (cls is Machine and k == "id")
    }
    try:
        obj = cls.from_dict(data, strict=False)  # type: ignore[attr-defined]
    except ValueError:
        return None
    return None if validate(obj) else obj


def _rule_set_errors(rules: Iterable[Rule], workflows: Iterable[Workflow]) -> list[dict[str, str]]:
    found = validate_rule_set(list(rules), {w.id: w for w in workflows})
    return [e.to_dict() for e in found]


class Definitions:
    """Create / update / enable / import of rules, workflows, actors and machines."""

    def __init__(
        self,
        store: StoragePort,
        audit: AuditLog | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._audit = audit or AuditLog(clock=self._clock)

    # ------------------------------------------------------------------ reads

    @staticmethod
    def _cls(kind: str) -> type:
        try:
            return DEFINITION_KINDS[kind]
        except KeyError:
            raise NotFound(f"unknown kind {kind!r}") from None

    def rule_set(self) -> tuple[list[Rule], dict[str, Workflow]]:
        """Live rules and workflows, parsed tolerantly; unparsable ones are skipped."""
        rules = [r for d in self.list("rules") if (r := _tolerant(Rule, d)) is not None]
        flows = [w for d in self.list("workflows") if (w := _tolerant(Workflow, d)) is not None]
        return rules, {w.id: w for w in flows}

    def list(self, kind: str, *, include_deleted: bool = False) -> list[Document]:
        self._cls(kind)
        docs = self._store.find(kind)
        return docs if include_deleted else [d for d in docs if _is_live(d)]

    def get(self, kind: str, id: str) -> Document:
        self._cls(kind)
        doc = self._store.get(kind, id)
        if doc is None:
            raise NotFound(f"{kind}/{id} does not exist")
        return doc

    # ------------------------------------------------------------------ writes

    def _parse(self, kind: str, body: Any) -> tuple[Any, str]:
        cls = self._cls(kind)
        obj, errors = validate_data(cls, body)
        if errors or obj is None:
            raise Invalid(f"{kind[:-1]} failed validation", [e.to_dict() for e in errors])
        ident = _ident(kind, obj)
        try:
            check_name(ident)
        except ValueError as exc:
            raise Invalid(str(exc), [{"path": "id", "code": "unsafe_id", "message": str(exc)}])
        return obj, ident

    @staticmethod
    def _stored(obj: Any, ident: str) -> dict[str, Any]:
        return {"id": ident, **obj.to_dict()}

    def _check_rule_set(self, ops: Any, obj: Rule) -> None:
        """Refuse a rule save that breaks the rule set (cycles, bad output references)."""
        others = []
        for doc in ops.find("rules"):
            if doc["id"] != obj.id and _is_live(doc):
                parsed = _tolerant(Rule, doc)
                if parsed is not None:
                    others.append(parsed)
        wfs = [w for d in ops.find("workflows") if (w := _tolerant(Workflow, d)) is not None]
        errors = _rule_set_errors([*others, obj], wfs)
        if errors:
            raise Invalid("rule set failed validation", errors)

    def _save(
        self, kind: str, body: Any, identity: str, verb: str, expect_id: str | None
    ) -> Document:
        require_identity(identity)
        obj, ident = self._parse(kind, body)
        if expect_id is not None and ident != expect_id:
            raise Invalid(
                "body id does not match the URL",
                [{"path": "id", "code": "id_mismatch", "message": f"{ident!r} != {expect_id!r}"}],
            )
        with self._store.transaction() as tx:
            before = tx.get(kind, ident)
            if expect_id is None and before is not None:
                raise Conflict(f"{kind}/{ident} already exists")
            if expect_id is not None:
                if before is None:
                    raise NotFound(f"{kind}/{ident} does not exist")
                if not _is_live(before):
                    raise Conflict(f"{kind}/{ident} is deleted; restore it first")
            if isinstance(obj, Rule):
                self._check_rule_set(tx, obj)
            after = tx.put(kind, self._stored(obj, ident))
            self._audit.write(
                tx,
                identity=identity,
                verb=verb,
                collection=kind,
                target_id=ident,
                before=before,
                after=after,
            )
        return after

    @mutating_verb("definitions.create", "Create a rule, workflow, actor or machine")
    def create(self, kind: str, body: Any, identity: str) -> Document:
        return self._save(kind, body, identity, "definitions.create", None)

    @mutating_verb("definitions.update", "Replace a rule, workflow, actor or machine")
    def update(self, kind: str, id: str, body: Any, identity: str) -> Document:
        return self._save(kind, body, identity, "definitions.update", id)

    @mutating_verb("definitions.set_enabled", "Enable or disable a definition")
    def set_enabled(self, kind: str, id: str, enabled: bool, identity: str) -> Document:
        require_identity(identity)
        self._cls(kind)
        with self._store.transaction() as tx:
            before = tx.get(kind, id)
            if before is None:
                raise NotFound(f"{kind}/{id} does not exist")
            if not _is_live(before):
                raise Conflict(f"{kind}/{id} is deleted; restore it first")
            res = tx.update_if(kind, id, {"enabled": before.get("enabled")}, {"enabled": enabled})
            if not res.won:
                raise Conflict(f"{kind}/{id} changed concurrently")
            self._audit.write(
                tx,
                identity=identity,
                verb="definitions.set_enabled",
                collection=kind,
                target_id=id,
                before=before,
                after=res.document,
            )
        return res.document

    # ------------------------------------------------------------------ export / import

    def export_files(self, fmt: str = "json") -> dict[str, str]:
        """Live definitions as ``<kind>/<id>.<ext>`` -> text (secrets as references only)."""
        bundle = exchange.bundle_from_store(_LiveView(self._store))
        try:
            return exchange.bundle_files(bundle, fmt)
        except ValueError as exc:
            raise Invalid(str(exc), [{"path": "format", "code": "format", "message": str(exc)}])

    def _plan(self, tx: Any, files: Mapping[str, str]) -> tuple[dict[str, Any], list]:
        read = exchange.read_files(files)
        errors = [e.to_dict() for e in read.errors]
        bundle: Bundle = read.bundle
        incoming = {r.id for r in bundle.rules}
        incoming_wf = {w.id for w in bundle.workflows}
        rules = [r for r in bundle.rules]
        rules += [
            p
            for d in tx.find("rules")
            if d["id"] not in incoming and _is_live(d) and (p := _tolerant(Rule, d)) is not None
        ]
        wfs = list(bundle.workflows) + [
            p
            for d in tx.find("workflows")
            if d["id"] not in incoming_wf and (p := _tolerant(Workflow, d)) is not None
        ]
        errors += _rule_set_errors(rules, wfs)
        changes: list[dict[str, str]] = []
        writes: list[tuple[str, dict[str, Any]]] = []
        for kind in (*KINDS, SECRETS):
            for obj in getattr(bundle, kind):
                new = self._import_form(kind, obj)
                current = tx.get(kind, new["id"])
                if current is None:
                    action = "add"
                elif self._import_form(kind, self._reparse(kind, current)) == new:
                    action = "unchanged"
                else:
                    action = "change"
                changes.append(
                    {"kind": kind, "id": new["id"], "path": f"{kind}/{new['id']}", "action": action}
                )
                if action != "unchanged":
                    writes.append((kind, new))
        return {"changes": changes, "errors": errors}, writes

    @staticmethod
    def _import_form(kind: str, obj: Any) -> dict[str, Any]:
        if kind == SECRETS:
            data = obj.to_dict()
            return {"id": data.pop("name"), **data}
        return {"id": obj.id, **obj.to_dict()}

    @staticmethod
    def _reparse(kind: str, stored: Mapping[str, Any]) -> Any:
        data = {k: v for k, v in stored.items() if k != "updated_at"}
        if kind == SECRETS:
            return SecretRef.from_dict(
                {
                    "name": stored["id"],
                    "ref": data.get("ref"),
                    **(
                        {"schema_version": data["schema_version"]}
                        if "schema_version" in data
                        else {}
                    ),
                },
                strict=False,
            )
        return KINDS[kind].from_dict(data, strict=False)  # type: ignore[attr-defined]

    @mutating_verb("definitions.import", "Import definition files (dry-run unless apply)")
    def import_files(
        self, files: Mapping[str, str], identity: str, *, apply: bool = False
    ) -> dict[str, Any]:
        require_identity(identity)
        with self._store.transaction() as tx:
            plan, writes = self._plan(tx, files)
            if plan["errors"]:
                raise Invalid("import failed validation", plan["errors"])
            plan["applied"] = False
            if not apply:
                return plan
            for kind, doc in writes:
                tx.put(kind, doc)
            self._audit.write(
                tx,
                identity=identity,
                verb="definitions.import",
                collection="import",
                target_id=f"import-{uuid.uuid4().hex[:12]}",
                before=None,
                after={"imported": sorted(f"{k}/{d['id']}" for k, d in writes)},
            )
            plan["applied"] = True
        return plan


class _LiveView:
    """A read-only StoreOps view that hides soft-deleted documents (for export)."""

    def __init__(self, store: Any) -> None:
        self._store = store

    def find(self, collection: str, where: Any = None, *, limit: int | None = None) -> list:
        return [d for d in self._store.find(collection, where, limit=limit) if _is_live(d)]


def dumps(value: Any) -> str:
    """Canonical JSON used for SSE payloads and exports."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
