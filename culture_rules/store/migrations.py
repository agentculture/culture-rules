"""Forward-only schema migrations, gated on a successful backup.

Register one step per (collection, major) - each step moves a document from
``from_major`` to a strictly greater ``to_major``; there are no down
migrations. :func:`migrate` plans every document's chain, then calls the
backup hook, and writes nothing unless the hook reports ok::

    registry = MigrationRegistry()

    @registry.register("rules", from_major=1, to_major=2)
    def rename(doc):
        doc["title"] = doc.pop("name")
        return doc

    migrate(store, registry, backup=lambda: s3_snapshot().ok)

Standard-library only; works on any StoragePort.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from culture_rules.store.port import Document, StoragePort, StoreError, VersionSkewError
from culture_rules.store.versioning import SchemaVersion

MigrationFn = Callable[[Document], Document]
BackupHook = Callable[[], Any]


class MigrationError(StoreError):
    """A migration is invalid (not forward-only, duplicate) or a chain has a gap."""


class BackupRequiredError(MigrationError):
    """The backup hook did not report ok, so no migration was run."""


@dataclass(frozen=True)
class MigrationStep:
    collection: str
    from_major: int
    to_major: int
    fn: MigrationFn


@dataclass
class MigrationReport:
    """Per-collection count of documents migrated, and the target major."""

    target_major: int
    migrated: dict[str, int] = field(default_factory=dict)


class MigrationRegistry:
    """Forward-only migration steps keyed by (collection, from_major)."""

    def __init__(self) -> None:
        self._steps: dict[tuple[str, int], MigrationStep] = {}

    def register(
        self,
        collection: str,
        from_major: int,
        to_major: int,
        fn: MigrationFn | None = None,
    ) -> Any:
        """Register ``fn`` (or decorate it) as the step ``from_major -> to_major``."""
        if not isinstance(from_major, int) or from_major < 1:
            raise MigrationError("from_major must be an int >= 1")
        if not isinstance(to_major, int) or to_major <= from_major:
            raise MigrationError(
                f"migrations are forward-only: {from_major} -> {to_major} is not allowed"
            )
        if (collection, from_major) in self._steps:
            raise MigrationError(f"a migration from {collection} major {from_major} exists")

        def add(func: MigrationFn) -> MigrationFn:
            self._steps[(collection, from_major)] = MigrationStep(
                collection, from_major, to_major, func
            )
            return func

        return add(fn) if fn is not None else add

    def collections(self) -> list[str]:
        return sorted({c for c, _ in self._steps})

    def chain(self, collection: str, from_major: int, target_major: int) -> list[MigrationStep]:
        """Steps taking a document from ``from_major`` to exactly ``target_major``."""
        steps: list[MigrationStep] = []
        current = from_major
        while current < target_major:
            step = self._steps.get((collection, current))
            if step is None or step.to_major > target_major:
                raise MigrationError(
                    f"no migration path for {collection} from major {current} to {target_major}"
                )
            steps.append(step)
            current = step.to_major
        return steps


def _backup_ok(result: Any) -> bool:
    if result is True:
        return True
    if isinstance(result, Mapping):
        return result.get("ok") is True
    return getattr(result, "ok", False) is True


def migrate(
    store: StoragePort,
    registry: MigrationRegistry,
    *,
    backup: BackupHook,
    target_major: int | None = None,
    collections: list[str] | None = None,
) -> MigrationReport:
    """Migrate older-major documents up to ``target_major`` (default: the node's major).

    Order: plan every chain (a gap raises :class:`MigrationError` before any
    backup), call ``backup()`` - it must return True, a mapping with
    ``ok: True`` or an object with ``.ok`` True, else
    :class:`BackupRequiredError` and nothing is written - then rewrite each
    document in its own transaction at ``"<target>.0"``. Documents already at
    or above the target are left alone, so re-running is a no-op.
    """
    node = SchemaVersion.parse(store.node_schema_version)
    target = node.major if target_major is None else target_major
    if target > node.major:
        raise VersionSkewError(
            f"cannot migrate to major {target}; this node supports up to {node.major}"
        )
    names = registry.collections() if collections is None else list(collections)

    plan: list[tuple[str, str, list[MigrationStep]]] = []
    for name in names:
        for doc in store.find(name):
            version = SchemaVersion.parse(doc.get("schema_version", "0.0"))
            if version.major < target:
                plan.append((name, doc["id"], registry.chain(name, version.major, target)))

    try:
        verdict = backup()
    except Exception as exc:
        raise BackupRequiredError(f"backup hook failed: {exc}") from exc
    if not _backup_ok(verdict):
        raise BackupRequiredError(f"backup hook did not report ok: {verdict!r}")

    report = MigrationReport(target_major=target, migrated=dict.fromkeys(names, 0))
    for name, doc_id, steps in plan:
        with store.transaction() as tx:
            doc = tx.get(name, doc_id)
            if doc is None:
                continue
            version = SchemaVersion.parse(doc.get("schema_version", "0.0"))
            if version.major >= target:
                continue
            if version.major != steps[0].from_major:
                steps = registry.chain(name, version.major, target)
            for step in steps:
                doc = step.fn(doc)
                if doc.get("id") != doc_id:
                    raise MigrationError(f"migration {name} {step.from_major} changed the id")
                doc["schema_version"] = f"{step.to_major}.0"
            tx.put(name, doc)
        report.migrated[name] += 1
    return report
