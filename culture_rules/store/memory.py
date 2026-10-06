"""In-memory :class:`~culture_rules.store.port.StoragePort` adapter.

For tests and single-process use. Standard-library only. Several handles
(``MemoryStore.peer``) can share one backing dataset, each acting as a node
with its own ``node_schema_version`` - this is how rolling upgrades and
cross-host races are exercised without a database.

Concurrency: one re-entrant lock serialises every operation, so conditional
updates are trivially atomic. A transaction holds that lock for its whole
duration (serialisable isolation); its writes are applied in place with an undo
log and published to the change feed only at commit.
"""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from culture_rules.model.variable import (
    validate_variable_name,
    validate_variable_value,
)
from culture_rules.store.port import (
    CURSOR_COLLECTION,
    VARIABLES_COLLECTION,
    Change,
    ChangeOp,
    Document,
    DuplicateKeyError,
    StoreError,
    UpdateResult,
    cursor_id,
)
from culture_rules.store.versioning import (
    SchemaVersion,
    matches,
    merge_changes,
    prepare_write,
    require_collection,
    require_id,
    utc_timestamp,
)

Clock = Callable[[], datetime]


class _Backend:
    """The shared dataset: documents per collection plus one global commit log."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cond = threading.Condition(self.lock)
        self.data: dict[str, dict[str, Document]] = {}
        self.log: list[Change] = []  # token of log[i] is str(i + 1)
        self.tx_owner: int | None = None

    def publish(self, entries: list[tuple[str, ChangeOp, str, Document | None]]) -> None:
        for collection, op, doc_id, document in entries:
            token = str(len(self.log) + 1)
            self.log.append(Change(token, collection, op, doc_id, document))
        if entries:
            self.cond.notify_all()


@dataclass
class _Tx:
    undo: list[tuple[str, str, Document | None]] = field(default_factory=list)
    pending: list[tuple[str, ChangeOp, str, Document | None]] = field(default_factory=list)
    closed: bool = False


class MemoryStore:
    """A StoragePort over process memory."""

    def __init__(
        self,
        node_schema_version: Any = "1.0",
        *,
        clock: Clock | None = None,
        _backend: _Backend | None = None,
    ) -> None:
        self._node = SchemaVersion.parse(node_schema_version)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._backend = _backend or _Backend()

    @property
    def node_schema_version(self) -> SchemaVersion:
        return self._node

    def peer(self, node_schema_version: Any = None, *, clock: Clock | None = None) -> MemoryStore:
        """Another node's handle on the same data (default: same schema version)."""
        version = self._node if node_schema_version is None else node_schema_version
        return MemoryStore(version, clock=clock or self._clock, _backend=self._backend)

    # ------------------------------------------------------------ internals

    def _guard(self, tx: _Tx | None) -> None:
        if tx is not None:
            if tx.closed:
                raise StoreError("transaction handle used after the transaction ended")
            return
        if self._backend.tx_owner == threading.get_ident():
            raise StoreError("use the transaction handle inside a transaction")

    def _now(self) -> str:
        return utc_timestamp(self._clock())

    def _write(
        self, tx: _Tx | None, collection: str, doc_id: str, new: Document | None, op: ChangeOp
    ) -> None:
        docs = self._backend.data.setdefault(collection, {})
        previous = docs.get(doc_id)
        if new is None:
            docs.pop(doc_id, None)
        else:
            docs[doc_id] = new
        entry = (collection, op, doc_id, copy.deepcopy(new))
        if tx is None:
            self._backend.publish([entry])
        else:
            tx.undo.append((collection, doc_id, previous))
            tx.pending.append(entry)

    def _get(self, tx: _Tx | None, collection: str, id: str) -> Document | None:
        require_collection(collection)
        require_id(id)
        with self._backend.lock:
            self._guard(tx)
            doc = self._backend.data.get(collection, {}).get(id)
            return copy.deepcopy(doc)

    def _find(
        self,
        tx: _Tx | None,
        collection: str,
        where: Mapping[str, Any] | None,
        limit: int | None,
    ) -> list[Document]:
        require_collection(collection)
        if limit is not None and (not isinstance(limit, int) or limit < 0):
            raise ValueError("limit must be a non-negative int or None")
        with self._backend.lock:
            self._guard(tx)
            docs = self._backend.data.get(collection, {})
            found = [copy.deepcopy(docs[k]) for k in sorted(docs) if matches(docs[k], where)]
        return found if limit is None else found[:limit]

    def _insert(self, tx: _Tx | None, collection: str, document: Mapping[str, Any]) -> Document:
        require_collection(collection)
        with self._backend.lock:
            self._guard(tx)
            new = prepare_write(document, None, self._node, self._now())
            if new["id"] in self._backend.data.get(collection, {}):
                raise DuplicateKeyError(f"{collection}/{new['id']} already exists")
            self._write(tx, collection, new["id"], new, "insert")
            return copy.deepcopy(new)

    def _put(self, tx: _Tx | None, collection: str, document: Mapping[str, Any]) -> Document:
        require_collection(collection)
        if not isinstance(document, Mapping):
            raise ValueError("document must be a mapping")
        doc_id = require_id(document.get("id"))
        with self._backend.lock:
            self._guard(tx)
            existing = self._backend.data.get(collection, {}).get(doc_id)
            new = prepare_write(document, existing, self._node, self._now())
            self._write(tx, collection, doc_id, new, "insert" if existing is None else "update")
            return copy.deepcopy(new)

    def _update_if(
        self,
        tx: _Tx | None,
        collection: str,
        id: str,
        expected: Mapping[str, Any],
        changes: Mapping[str, Any],
        upsert: bool,
    ) -> UpdateResult:
        require_collection(collection)
        require_id(id)
        with self._backend.lock:
            self._guard(tx)
            existing = self._backend.data.get(collection, {}).get(id)
            if existing is None:
                if not upsert or any(v is not None for v in expected.values()):
                    return UpdateResult(False, None)
                op: ChangeOp = "insert"
            elif not matches(existing, expected):
                return UpdateResult(False, copy.deepcopy(existing))
            else:
                op = "update"
            merged = merge_changes(existing, id, changes)
            new = prepare_write(merged, existing, self._node, self._now())
            self._write(tx, collection, id, new, op)
            return UpdateResult(True, copy.deepcopy(new))

    def _delete(self, tx: _Tx | None, collection: str, id: str) -> bool:
        require_collection(collection)
        require_id(id)
        with self._backend.lock:
            self._guard(tx)
            if id not in self._backend.data.get(collection, {}):
                return False
            self._write(tx, collection, id, None, "delete")
            return True

    # ------------------------------------------------------------- StoreOps

    def get(self, collection: str, id: str) -> Document | None:
        return self._get(None, collection, id)

    def find(
        self,
        collection: str,
        where: Mapping[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[Document]:
        return self._find(None, collection, where, limit)

    def insert(self, collection: str, document: Mapping[str, Any]) -> Document:
        return self._insert(None, collection, document)

    def put(self, collection: str, document: Mapping[str, Any]) -> Document:
        return self._put(None, collection, document)

    def update_if(
        self,
        collection: str,
        id: str,
        expected: Mapping[str, Any],
        changes: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> UpdateResult:
        return self._update_if(None, collection, id, expected, changes, upsert)

    def delete(self, collection: str, id: str) -> bool:
        return self._delete(None, collection, id)

    # ---------------------------------------------------------- StoragePort

    @contextmanager
    def transaction(self) -> Iterator[_TxHandle]:
        backend = self._backend
        with backend.lock:
            self._guard(None)
            backend.tx_owner = threading.get_ident()
            tx = _Tx()
            try:
                yield _TxHandle(self, tx)
            except BaseException:
                for collection, doc_id, previous in reversed(tx.undo):
                    docs = backend.data.setdefault(collection, {})
                    if previous is None:
                        docs.pop(doc_id, None)
                    else:
                        docs[doc_id] = previous
                raise
            else:
                backend.publish(tx.pending)
            finally:
                tx.closed = True
                backend.tx_owner = None

    def head(self, collection: str) -> str:
        require_collection(collection)
        with self._backend.lock:
            self._guard(None)
            return str(len(self._backend.log))

    def changes(self, collection: str, after: str, *, timeout: float = 0.0) -> Iterator[Change]:
        require_collection(collection)
        backend = self._backend
        with backend.lock:
            self._guard(None)
            try:
                start = int(after)
            except (TypeError, ValueError):
                raise ValueError(f"invalid change-feed token: {after!r}") from None
            if start < 0 or start > len(backend.log):
                raise ValueError(f"change-feed token {after!r} was not issued by this store")

            def pending() -> list[Change]:
                return [c for c in backend.log[start:] if c.collection == collection]

            found = pending()
            if not found and timeout > 0:
                backend.cond.wait_for(lambda: bool(pending()), timeout=timeout)
                found = pending()
        return iter([replace(c, document=copy.deepcopy(c.document)) for c in found])

    def save_cursor(self, consumer: str, collection: str, token: str) -> None:
        if not isinstance(token, str) or not token:
            raise ValueError("token must be a non-empty string")
        self.put(
            CURSOR_COLLECTION,
            {
                "id": cursor_id(consumer, collection),
                "consumer": consumer,
                "collection": collection,
                "token": token,
            },
        )

    def load_cursor(self, consumer: str, collection: str) -> str | None:
        doc = self.get(CURSOR_COLLECTION, cursor_id(consumer, collection))
        return None if doc is None else doc.get("token")

    # --------------------------------------------------------------- variables

    def _validate_variable_name(self, name: str) -> None:
        validate_variable_name(name)

    @staticmethod
    def _validate_variable_value(value: Any) -> None:
        validate_variable_value(value)

    @staticmethod
    def _variable_view(name: str, version: Mapping[str, Any]) -> Document:
        """The flat, single-version view of a stored variable document."""
        return {
            "id": name,
            "name": name,
            "value": copy.deepcopy(version["value"]),
            "version": version["version"],
            "updated_by": version["updated_by"],
            "updated_at": version["updated_at"],
            "description": version.get("description"),
        }

    def put_variable(
        self, name: str, value: Any, *, updated_by: str, description: str | None = None
    ) -> Document:
        self._validate_variable_name(name)
        self._validate_variable_value(value)
        now = self._now()
        while True:
            existing = self._get(None, VARIABLES_COLLECTION, name)
            versions: list[Document] = list(existing.get("versions", [])) if existing else []
            entry: Document = {
                "version": (versions[-1]["version"] if versions else 0) + 1,
                "value": copy.deepcopy(value),
                "updated_by": updated_by,
                "updated_at": now,
                "description": description,
            }
            result = self._update_if(
                None,
                VARIABLES_COLLECTION,
                name,
                expected={"versions": existing.get("versions") if existing else None},
                changes={"name": name, "versions": versions + [entry]},
                upsert=existing is None,
            )
            if result.won:
                view = self._variable_view(name, result.document["versions"][-1])
                view["schema_version"] = result.document.get("schema_version")
                view["updated_at"] = result.document.get("updated_at", view.get("updated_at"))
                return view

    def get_variable(self, name: str) -> Document | None:
        doc = self._get(None, VARIABLES_COLLECTION, name)
        if doc is None:
            return None
        versions = doc.get("versions", [])
        return None if not versions else self._variable_view(doc["name"], versions[-1])

    def get_variable_version(self, name: str, version: int) -> Document | None:
        doc = self._get(None, VARIABLES_COLLECTION, name)
        if doc is None:
            return None
        for v in doc.get("versions", []):
            if v["version"] == version:
                return self._variable_view(doc["name"], v)
        return None

    def list_variables(self) -> list[Document]:
        result: list[Document] = []
        for doc in self._find(None, VARIABLES_COLLECTION, None, None):
            versions = doc.get("versions", [])
            if versions:
                result.append(self._variable_view(doc["name"], versions[-1]))
        result.sort(key=lambda d: d["name"])
        return result


class _TxHandle:
    """StoreOps bound to one open MemoryStore transaction."""

    def __init__(self, store: MemoryStore, tx: _Tx) -> None:
        self._store = store
        self._tx = tx

    def get(self, collection: str, id: str) -> Document | None:
        return self._store._get(self._tx, collection, id)

    def find(
        self,
        collection: str,
        where: Mapping[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[Document]:
        return self._store._find(self._tx, collection, where, limit)

    def insert(self, collection: str, document: Mapping[str, Any]) -> Document:
        return self._store._insert(self._tx, collection, document)

    def put(self, collection: str, document: Mapping[str, Any]) -> Document:
        return self._store._put(self._tx, collection, document)

    def update_if(
        self,
        collection: str,
        id: str,
        expected: Mapping[str, Any],
        changes: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> UpdateResult:
        return self._store._update_if(self._tx, collection, id, expected, changes, upsert)

    def delete(self, collection: str, id: str) -> bool:
        return self._store._delete(self._tx, collection, id)
