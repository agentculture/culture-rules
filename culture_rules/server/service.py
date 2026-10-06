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
from culture_rules.io import exchange, gitrepo
from culture_rules.io.bundle import KINDS, Bundle, SecretRef, check_name
from culture_rules.model.machine import Machine
from culture_rules.model.rule import Rule
from culture_rules.model.validate import validate, validate_data
from culture_rules.model.variable import validate_variable_name
from culture_rules.model.variable_refs import rule_variable_refs
from culture_rules.model.workflow import Workflow
from culture_rules.store.port import Document, StoragePort

__all__ = [
    "DEFINITION_KINDS",
    "Conflict",
    "Definitions",
    "Invalid",
    "RuleReferenced",
    "NotFound",
    "ServiceError",
    "Variables",
]

#: kind -> model class; the kind is also the store collection.
DEFINITION_KINDS: dict[str, type] = {**KINDS, "machines": Machine}
SECRETS = "secrets"


class ServiceError(Exception):
    """Base class: ``code`` is a stable machine-readable string, ``errors`` the details."""

    code = "error"

    def __init__(
        self,
        message: str,
        errors: Iterable[Mapping[str, Any]] = (),
        *,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.errors = [dict(e) for e in errors]
        if code:
            self.code = code


class Invalid(ServiceError):
    """The input failed validation (HTTP 422)."""

    code = "invalid"


class NotFound(ServiceError):
    """The target does not exist (HTTP 404)."""

    code = "not_found"


class Conflict(ServiceError):
    """The target is in the wrong state for this verb (HTTP 409)."""

    code = "conflict"


class RuleReferenced(Conflict):
    """Other live rules still reference this rule (HTTP 409)."""

    code = "rule_referenced"


def _ident(kind: str, obj: Any) -> str:
    return obj.name if kind == "machines" else obj.id


def _is_live(doc: Mapping[str, Any]) -> bool:
    return not doc.get("deleted_at")


def _tolerant(cls: type, doc: Mapping[str, Any]) -> Any | None:
    """Parse a stored document, ignoring unknown fields; ``None`` if unparsable or invalid.

    Validated in stored mode: a rule saved before the save-time catalog checks still counts.

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
    return None if validate(obj, stored=True) else obj


#: ``(kind, stored document or None, document about to be written)``; raises to refuse the
#: save. Runs inside the write transaction, so it sees the version it compares against
#: (``culture_rules.auth.guards.save_check`` builds the API's).
SaveCheck = Callable[[str, "Mapping[str, Any] | None", Mapping[str, Any]], None]


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

    def referrers(self, rule_id: str) -> list[str]:
        """Ids of live rules whose must_after/may_after/supersedes name ``rule_id``."""
        out = []
        for doc in self.list("rules"):
            if doc.get("id") == rule_id:
                continue
            refs = [*(doc.get("must_after") or ()), *(doc.get("may_after") or ())]
            if rule_id in [*refs, *(doc.get("supersedes") or ())]:
                out.append(str(doc.get("id")))
        return sorted(out)

    def guard_unreferenced(self, kind: str, id: str) -> None:
        """Refuse to remove a rule other live rules still reference (409 ``rule_referenced``)."""
        if kind != "rules":
            return
        refs = self.referrers(id)
        if refs:
            raise RuleReferenced(
                f"rules/{id} is referenced by: {', '.join(refs)}",
                [{"path": f"rules/{r}", "code": "rule_referenced", "message": r} for r in refs],
            )

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
        self,
        kind: str,
        body: Any,
        identity: str,
        verb: str,
        expect_id: str | None,
        check: SaveCheck | None = None,
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
            doc = self._stored(obj, ident)
            if check is not None:
                check(kind, before, doc)
            after = tx.put(kind, doc)
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
    def create(
        self, kind: str, body: Any, identity: str, *, check: SaveCheck | None = None
    ) -> Document:
        return self._save(kind, body, identity, "definitions.create", None, check)

    @mutating_verb("definitions.update", "Replace a rule, workflow, actor or machine")
    def update(
        self, kind: str, id: str, body: Any, identity: str, *, check: SaveCheck | None = None
    ) -> Document:
        return self._save(kind, body, identity, "definitions.update", id, check)

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

    def live_bundle(self) -> Bundle:
        """Every live (not soft-deleted) definition as a :class:`Bundle`."""
        return exchange.bundle_from_store(_LiveView(self._store))

    def export_files(self, fmt: str = "json") -> dict[str, str]:
        """Live definitions as ``<kind>/<id>.<ext>`` -> text (secrets as references only)."""
        try:
            return exchange.bundle_files(self.live_bundle(), fmt)
        except ValueError as exc:
            raise Invalid(str(exc), [{"path": "format", "code": "format", "message": str(exc)}])

    # ------------------------------------------------------------------ repositories

    @mutating_verb("definitions.export_repo", "Export definitions into a configured git repo")
    def export_to_repo(
        self,
        target: Any,
        identity: str,
        *,
        fmt: str = "json",
        directory: str = "",
        apply: bool = False,
        push: bool = False,
    ) -> dict[str, Any]:
        """Write live definitions into ``target`` (a local working tree) and commit them.

        Dry-run unless ``apply``: the plan lists each file's action and nothing is written.
        ``push`` (only with ``apply``) runs ``git push origin HEAD`` after the commit. An
        applied export writes one audit entry, whether or not anything changed.
        """
        require_identity(identity)
        if not target.writable:
            raise Invalid(
                f"repo {target.name!r} is not a local working tree; exports need one",
                [{"path": "repo", "code": "repo_not_local", "message": target.location}],
                code="repo_not_local",
            )
        try:
            saved = gitrepo.save_to_repo(
                self.live_bundle(),
                target.location,
                directory=directory,
                fmt=fmt,
                apply=apply,
                push=push and apply,
            )
        except gitrepo.GitError as exc:
            raise Invalid(
                str(exc), [{"path": "repo", "code": "git", "message": str(exc)}], code="git_error"
            ) from exc
        except ValueError as exc:
            raise Invalid(
                str(exc), [{"path": "directory", "code": "invalid", "message": str(exc)}]
            ) from exc
        result = {
            "repo": target.name,
            "applied": saved.plan.applied,
            "committed": saved.committed,
            "pushed": saved.pushed,
            "commit": saved.commit,
            "changes": [
                {"kind": c.kind, "id": c.id, "path": c.path, "action": c.action}
                for c in saved.plan.changes
            ],
        }
        if apply:
            self._audit.write(
                self._store,
                identity=identity,
                verb="definitions.export_repo",
                collection="export",
                target_id=f"export-{uuid.uuid4().hex[:12]}",
                before=None,
                after={
                    "repo": target.name,
                    "commit": saved.commit,
                    "pushed": saved.pushed,
                    "written": sorted(
                        c.path for c in saved.plan.changes if c.action != "unchanged"
                    ),
                },
            )
        return result

    @staticmethod
    def repo_files(target: Any, *, directory: str = "", ref: str | None = None) -> dict[str, str]:
        """Definition files read from a clone of ``target`` (refused with every read error)."""
        try:
            read = gitrepo.load_from_repo(target.location, directory=directory, ref=ref)
        except gitrepo.GitError as exc:
            raise Invalid(
                str(exc), [{"path": "repo", "code": "git", "message": str(exc)}], code="git_error"
            ) from exc
        except ValueError as exc:
            raise Invalid(
                str(exc), [{"path": "directory", "code": "invalid", "message": str(exc)}]
            ) from exc
        if read.errors:
            raise Invalid("repo definitions failed validation", [e.to_dict() for e in read.errors])
        try:
            return exchange.bundle_files(read.bundle, "json")
        except ValueError as exc:
            raise Invalid(str(exc), [{"path": "repo", "code": "invalid", "message": str(exc)}])

    def _plan(
        self, tx: Any, files: Mapping[str, str], check: SaveCheck | None = None
    ) -> tuple[dict[str, Any], list]:
        read = exchange.read_files(files)
        errors = [e.to_dict() for e in read.errors]
        bundle: Bundle = read.bundle
        incoming = {r.id for r in bundle.rules}
        incoming_wf = {w.id for w in bundle.workflows}
        rules = list(bundle.rules)
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
                if check is not None:
                    check(kind, current, new)
                action = self._import_action(kind, current, new)
                changes.append(
                    {"kind": kind, "id": new["id"], "path": f"{kind}/{new['id']}", "action": action}
                )
                if action != "unchanged":
                    writes.append((kind, new))
        return {"changes": changes, "errors": errors}, writes

    def _import_action(self, kind: str, current: Any, new: dict[str, Any]) -> str:
        """``add``, ``unchanged`` or ``change``: what importing ``new`` does to ``current``."""
        if current is None:
            return "add"
        if self._import_form(kind, self._reparse(kind, current)) == new:
            return "unchanged"
        return "change"

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
        self,
        files: Mapping[str, str],
        identity: str,
        *,
        apply: bool = False,
        check: SaveCheck | None = None,
    ) -> dict[str, Any]:
        require_identity(identity)
        with self._store.transaction() as tx:
            plan, writes = self._plan(tx, files, check)
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


class Variables:
    """Shared variables behind the API: reads, an append-only write, history and referrers.

    Authorization (admin-only writes) is the route matrix's job; this class records whichever
    identity it is handed as ``updated_by``.
    """

    def __init__(self, store: StoragePort) -> None:
        self._store = store

    @staticmethod
    def _checked(name: str) -> str:
        try:
            validate_variable_name(name)
        except ValueError as exc:
            raise Invalid(str(exc), [{"path": "name", "code": "invalid_name", "message": str(exc)}])
        return name

    def list(self) -> list[Document]:
        return self._store.list_variables()

    def get(self, name: str) -> Document:
        self._checked(name)
        doc = self._store.get_variable(name)
        if doc is None:
            raise NotFound(f"variables/{name} does not exist")
        return doc

    def set(self, name: str, value: Any, identity: str, description: str | None = None) -> Document:
        self._checked(name)
        try:
            return self._store.put_variable(
                name, value, updated_by=require_identity(identity), description=description
            )
        except ValueError as exc:
            raise Invalid(
                str(exc), [{"path": "value", "code": "invalid_value", "message": str(exc)}]
            )

    def history(self, name: str) -> list[Document]:
        """Every version, oldest first (the store is append-only, so versions are 1..latest)."""
        latest = self.get(name)["version"]
        out = [self._store.get_variable_version(name, n) for n in range(1, latest + 1)]
        return [v for v in out if v is not None]

    def refs(self, name: str) -> list[Document]:
        """Live rules whose condition or workflow inputs reference ``name``, by id."""
        self._checked(name)
        docs = [d for d in self._store.find("rules") if _is_live(d)]
        hits = [d for d in docs if name in rule_variable_refs(d)]
        return [
            {"id": d.get("id"), "name": d.get("name"), "enabled": d.get("enabled", True)}
            for d in sorted(hits, key=lambda d: str(d.get("id")))
        ]


def dumps(value: Any) -> str:
    """Canonical JSON used for SSE payloads and exports."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
