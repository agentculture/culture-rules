"""MongoDB replica-set :class:`~culture_rules.store.port.StoragePort` adapter.

``pymongo`` is an optional dependency (``pip install culture-rules[store]``) and
is imported lazily, so importing this module never needs it. The adapter holds
no state of its own: every document, change-feed cursor and lease lives in the
database, nothing is written to local files, and any number of nodes can open
the same database through independent :class:`MongoStore` instances.

Configuration comes from the environment (or an explicit :class:`MongoConfig`),
never from a default: there is deliberately no built-in URI or port.

``CULTURE_RULES_MONGO_URI``
    Required. A ``mongodb://`` or ``mongodb+srv://`` URI that names a replica
    set and carries credentials (or X.509 auth).
``CULTURE_RULES_MONGO_DB``
    Database name (default ``culture_rules``).
``CULTURE_RULES_MONGO_TLS_CA_FILE``
    CA bundle that signed the server certificate (default: system trust store).
``CULTURE_RULES_MONGO_TLS_CERT_KEY_FILE``
    Client certificate + key (for X.509 authentication / mutual TLS).

Security posture (enforced, not merely recommended):

- the connection is always TLS (``tls=True``; a URI that says ``tls=false`` is
  refused) and always authenticated (a URI without credentials is refused);
- the connecting user must hold no administrative role
  (:meth:`MongoStore.verify_least_privilege`, run at connect time);
- every write uses ``w=majority`` and every read ``readConcern=majority``.

Change feeds use MongoDB change streams with ``changeStreamPreAndPostImages``
enabled per collection and ``fullDocument="required"``, so each
:class:`~culture_rules.store.port.Change` carries the post-image as of its own
commit. A feed token is the change stream's resume token (its ``_data`` string);
:meth:`MongoStore.save_cursor` persists it per consumer in the ``_cursors``
collection of the same database.

Compare-and-set (``update_if``) and replace (``put``) read the document, apply
the shared envelope rules from :mod:`culture_rules.store.versioning`, and swap
it with a filter on the exact document that was read, retrying if another node
changed it in between; the loser of a race therefore re-reads and loses
cleanly. Collections are created on first use (change-stream images need the
option set at creation); inside a transaction, touch new collections with
:meth:`MongoStore.ensure_collections` first.
"""

from __future__ import annotations

import contextlib
import importlib
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from culture_rules.store.port import (
    CURSOR_COLLECTION,
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

ENV_URI = "CULTURE_RULES_MONGO_URI"
ENV_DB = "CULTURE_RULES_MONGO_DB"
ENV_TLS_CA = "CULTURE_RULES_MONGO_TLS_CA_FILE"
ENV_TLS_CERT_KEY = "CULTURE_RULES_MONGO_TLS_CERT_KEY_FILE"
DEFAULT_DATABASE = "culture_rules"

_ADMIN_ROLES = frozenset(
    {
        "root",
        "dbOwner",
        "dbAdmin",
        "userAdmin",
        "clusterAdmin",
        "clusterManager",
        "hostManager",
        "backup",
        "restore",
        "readAnyDatabase",
        "readWriteAnyDatabase",
        "userAdminAnyDatabase",
        "dbAdminAnyDatabase",
        "__system",
    }
)
_MAX_ATTEMPTS = 100
_SETTLE_MS = 100  # how long an empty change-stream poll waits to call the feed "caught up"


class ConfigError(StoreError):
    """The MongoDB configuration is missing, insecure, or the driver is not installed."""


def _pymongo() -> Any:
    try:
        return importlib.import_module("pymongo")
    except ImportError as exc:
        raise ConfigError(
            "the MongoDB adapter needs pymongo: pip install 'culture-rules[store]'"
        ) from exc


@dataclass(frozen=True)
class MongoConfig:
    """Where and how to reach the replica set."""

    uri: str
    database: str = DEFAULT_DATABASE
    tls_ca_file: str | None = None
    tls_cert_key_file: str | None = None

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> MongoConfig:
        import os

        env = os.environ if environ is None else environ
        uri = env.get(ENV_URI, "").strip()
        if not uri:
            raise ConfigError(f"{ENV_URI} is not set; there is no default MongoDB address")
        return cls(
            uri=uri,
            database=env.get(ENV_DB) or DEFAULT_DATABASE,
            tls_ca_file=env.get(ENV_TLS_CA) or None,
            tls_cert_key_file=env.get(ENV_TLS_CERT_KEY) or None,
        )


def build_client_kwargs(config: MongoConfig) -> dict[str, Any]:
    """The MongoClient options this adapter always applies: TLS, majority, retryable writes."""
    kwargs: dict[str, Any] = {
        "tls": True,
        "w": "majority",
        "readConcernLevel": "majority",
        "retryWrites": True,
        "appname": "culture-rules",
    }
    if config.tls_ca_file:
        kwargs["tlsCAFile"] = config.tls_ca_file
    if config.tls_cert_key_file:
        kwargs["tlsCertificateKeyFile"] = config.tls_cert_key_file
    return kwargs


def _validate(config: MongoConfig, pymongo: Any) -> None:
    from pymongo import uri_parser  # noqa: PLC0415 - lazy, after _pymongo() succeeded

    del pymongo
    try:
        parsed = uri_parser.parse_uri(config.uri, validate=True)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a config error
        raise ConfigError(f"invalid MongoDB URI: {exc}") from exc
    options = parsed.get("options", {})
    if options.get("tls") is False or options.get("ssl") is False:
        raise ConfigError("TLS is required: the URI disables it (tls=false)")
    mechanism = options.get("authmechanism") or options.get("authMechanism")
    if not parsed.get("username") and mechanism != "MONGODB-X509":
        raise ConfigError("authentication is required: the URI carries no credentials")
    if mechanism == "MONGODB-X509" and not config.tls_cert_key_file:
        raise ConfigError("X.509 authentication needs tls_cert_key_file")


def _to_doc(raw: Mapping[str, Any] | None) -> Document | None:
    if raw is None:
        return None
    doc = dict(raw)
    doc_id = doc.pop("_id")
    return {"id": doc_id, **doc}


def _to_raw(doc: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(doc)
    raw["_id"] = raw.pop("id")
    return raw


def _translate_where(where: Mapping[str, Any] | None) -> dict[str, Any]:
    return {("_id" if k == "id" else k): v for k, v in (where or {}).items()}


class MongoStore:
    """A StoragePort over a MongoDB replica set (majority writes, change streams)."""

    def __init__(
        self,
        config: MongoConfig,
        node_schema_version: Any = "1.0",
        *,
        connect: bool = True,
        server_selection_timeout_ms: int = 10000,
        **client_options: Any,
    ) -> None:
        pymongo = _pymongo()
        _validate(config, pymongo)
        self.config = config
        self._node = SchemaVersion.parse(node_schema_version)
        options = {"serverSelectionTimeoutMS": server_selection_timeout_ms, **client_options}
        self._client = pymongo.MongoClient(config.uri, **{**options, **build_client_kwargs(config)})
        self._db = self._client[config.database]
        self._ensured: set[str] = set()
        if connect:
            try:
                self.verify_least_privilege()
            except BaseException:
                self.close()
                raise

    # ------------------------------------------------------------ connection

    @property
    def client(self) -> Any:
        return self._client

    @property
    def node_schema_version(self) -> SchemaVersion:
        return self._node

    def close(self) -> None:
        self._client.close()

    def verify_least_privilege(self) -> None:
        """Authenticate, then refuse a user that holds any administrative role."""
        status = self._client.admin.command("connectionStatus")
        roles = status.get("authInfo", {}).get("authenticatedUserRoles", [])
        if not status.get("authInfo", {}).get("authenticatedUsers"):
            raise ConfigError("authentication is required: the connection is not authenticated")
        bad = [r for r in roles if r.get("db") == "admin" or r.get("role") in _ADMIN_ROLES]
        if bad:
            raise ConfigError(
                "the application user must not hold an admin role; found "
                + ", ".join(f"{r['role']}@{r['db']}" for r in bad)
            )

    # ------------------------------------------------------------ collections

    def _collection(self, name: str) -> Any:
        require_collection(name)
        self._ensure(name)
        return self._db.get_collection(name)

    def _ensure(self, name: str) -> None:
        if name in self._ensured:
            return
        from pymongo.errors import CollectionInvalid, OperationFailure

        try:
            self._db.create_collection(name, changeStreamPreAndPostImages={"enabled": True})
        except (CollectionInvalid, OperationFailure) as exc:
            if isinstance(exc, OperationFailure) and exc.code != 48:  # NamespaceExists
                raise
            self._ensure_images_enabled(name)
        self._ensured.add(name)

    def _ensure_images_enabled(self, name: str) -> None:
        (info,) = list(self._db.list_collections(filter={"name": name}))
        enabled = info.get("options", {}).get("changeStreamPreAndPostImages", {}).get("enabled")
        if not enabled:
            self._db.command("collMod", name, changeStreamPreAndPostImages={"enabled": True})

    def ensure_collections(self, *names: str) -> None:
        """Create collections up front (do this before a transaction that spans new ones)."""
        for name in names:
            require_collection(name)
            self._ensure(name)

    # ---------------------------------------------------------------- ops

    def _now(self) -> str:
        return utc_timestamp()

    def _insert(self, session: Any, collection: str, document: Mapping[str, Any]) -> Document:
        from pymongo.errors import DuplicateKeyError as MongoDuplicate

        coll = self._collection(collection)
        _reject_raw_id(document)
        new = prepare_write(document, None, self._node, self._now())
        try:
            coll.insert_one(_to_raw(new), session=session)
        except MongoDuplicate:
            raise DuplicateKeyError(f"{collection}/{new['id']} already exists") from None
        return new

    def _put(self, session: Any, collection: str, document: Mapping[str, Any]) -> Document:
        from pymongo.errors import DuplicateKeyError as MongoDuplicate

        if not isinstance(document, Mapping):
            raise ValueError("document must be a mapping")
        _reject_raw_id(document)
        doc_id = require_id(document.get("id"))
        coll = self._collection(collection)
        for _ in range(_MAX_ATTEMPTS):
            raw = coll.find_one({"_id": doc_id}, session=session)
            new = prepare_write(document, _to_doc(raw), self._node, self._now())
            try:
                if raw is None:
                    coll.insert_one(_to_raw(new), session=session)
                    return new
                if coll.replace_one(raw, _to_raw(new), session=session).matched_count:
                    return new
            except MongoDuplicate:
                pass
            _raise_if_in_transaction(session)
        raise StoreError(f"put {collection}/{doc_id}: too much contention")

    def _update_if(
        self,
        session: Any,
        collection: str,
        id: str,
        expected: Mapping[str, Any],
        changes: Mapping[str, Any],
        upsert: bool,
    ) -> UpdateResult:
        from pymongo.errors import DuplicateKeyError as MongoDuplicate

        require_id(id)
        coll = self._collection(collection)
        for _ in range(_MAX_ATTEMPTS):
            raw = coll.find_one({"_id": id}, session=session)
            existing = _to_doc(raw)
            if existing is None:
                if not upsert or any(v is not None for v in expected.values()):
                    return UpdateResult(False, None)
            elif not matches(existing, expected):
                return UpdateResult(False, existing)
            new = prepare_write(
                merge_changes(existing, id, changes), existing, self._node, self._now()
            )
            try:
                if raw is None:
                    coll.insert_one(_to_raw(new), session=session)
                    return UpdateResult(True, new)
                if coll.replace_one(raw, _to_raw(new), session=session).matched_count:
                    return UpdateResult(True, new)
            except MongoDuplicate:
                pass
            _raise_if_in_transaction(session)
        raise StoreError(f"update_if {collection}/{id}: too much contention")

    def _get(self, session: Any, collection: str, id: str) -> Document | None:
        require_id(id)
        return _to_doc(self._collection(collection).find_one({"_id": id}, session=session))

    def _find(
        self,
        session: Any,
        collection: str,
        where: Mapping[str, Any] | None,
        limit: int | None,
    ) -> list[Document]:
        if limit is not None and (not isinstance(limit, int) or limit < 0):
            raise ValueError("limit must be a non-negative int or None")
        cursor = self._collection(collection).find(_translate_where(where), session=session)
        cursor = cursor.sort("_id", 1)
        if limit:
            cursor = cursor.limit(limit)
        if limit == 0:
            return []
        return [_to_doc(raw) for raw in cursor]

    def _delete(self, session: Any, collection: str, id: str) -> bool:
        require_id(id)
        result = self._collection(collection).delete_one({"_id": id}, session=session)
        return result.deleted_count > 0

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
        pymongo = _pymongo()
        with self._client.start_session() as session:
            session.start_transaction(
                read_concern=pymongo.read_concern.ReadConcern("snapshot"),
                write_concern=pymongo.WriteConcern("majority"),
            )
            handle = _TxHandle(self, session)
            try:
                yield handle
            except BaseException:
                handle._closed = True
                with contextlib.suppress(Exception):  # the original error matters more
                    session.abort_transaction()
                raise
            handle._closed = True
            session.commit_transaction()

    def _open_stream(self, collection: str, token: str | None, wait_ms: int) -> Any:
        coll = self._collection(collection)
        options: dict[str, Any] = {"full_document": "required", "max_await_time_ms": wait_ms}
        if token is not None:
            options["resume_after"] = {"_data": token}
        return coll.watch(**options)

    def head(self, collection: str) -> str:
        require_collection(collection)
        with self._open_stream(collection, None, _SETTLE_MS) as stream:
            token = stream.resume_token
            if token is None:
                stream.try_next()
                token = stream.resume_token
        if token is None:
            raise StoreError(f"could not obtain a change-stream position for {collection!r}")
        return token["_data"]

    def changes(self, collection: str, after: str, *, timeout: float = 0.0) -> Iterator[Change]:
        from pymongo.errors import PyMongoError

        require_collection(collection)
        if not isinstance(after, str) or not after:
            raise ValueError(f"invalid change-feed token: {after!r}")
        wait_ms = _SETTLE_MS if timeout <= 0 else max(_SETTLE_MS, min(int(timeout * 1000), 250))
        deadline = time.monotonic() + max(timeout, 0.0)
        found: list[Change] = []
        try:
            with self._open_stream(collection, after, wait_ms) as stream:
                while True:
                    event = stream.try_next()
                    if event is not None:
                        change = _to_change(collection, event)
                        if change is not None:
                            found.append(change)
                        continue
                    if found or time.monotonic() >= deadline:
                        break
        except PyMongoError as exc:
            raise StoreError(f"change feed for {collection!r} failed: {exc}") from exc
        return iter(found)

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


_OPS: dict[str, ChangeOp] = {
    "insert": "insert",
    "update": "update",
    "replace": "update",
    "delete": "delete",
}


def _to_change(collection: str, event: Mapping[str, Any]) -> Change | None:
    kind = event["operationType"]
    if kind not in _OPS:
        if kind in {"drop", "dropDatabase", "rename", "invalidate"}:
            raise StoreError(f"collection {collection!r} feed ended: {kind}")
        return None
    return Change(
        token=event["_id"]["_data"],
        collection=collection,
        op=_OPS[kind],
        id=event["documentKey"]["_id"],
        document=_to_doc(event.get("fullDocument")),
    )


def _reject_raw_id(document: Any) -> None:
    if isinstance(document, Mapping) and "_id" in document:
        raise ValueError("documents use 'id'; '_id' is reserved by the adapter")


def _raise_if_in_transaction(session: Any) -> None:
    if session is not None:
        raise StoreError("a concurrent write conflicted inside the transaction; retry it")


class _TxHandle:
    """StoreOps bound to one open MongoDB transaction."""

    def __init__(self, store: MongoStore, session: Any) -> None:
        self._store = store
        self._session = session
        self._closed = False

    def _live(self) -> Any:
        if self._closed:
            raise StoreError("transaction handle used after the transaction ended")
        return self._session

    def get(self, collection: str, id: str) -> Document | None:
        return self._store._get(self._live(), collection, id)

    def find(
        self,
        collection: str,
        where: Mapping[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[Document]:
        return self._store._find(self._live(), collection, where, limit)

    def insert(self, collection: str, document: Mapping[str, Any]) -> Document:
        return self._store._insert(self._live(), collection, document)

    def put(self, collection: str, document: Mapping[str, Any]) -> Document:
        return self._store._put(self._live(), collection, document)

    def update_if(
        self,
        collection: str,
        id: str,
        expected: Mapping[str, Any],
        changes: Mapping[str, Any],
        *,
        upsert: bool = False,
    ) -> UpdateResult:
        return self._store._update_if(self._live(), collection, id, expected, changes, upsert)

    def delete(self, collection: str, id: str) -> bool:
        return self._store._delete(self._live(), collection, id)
