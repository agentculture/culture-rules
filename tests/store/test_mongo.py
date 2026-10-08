"""Run the shared StoragePort contract against a real MongoDB replica set (docker).

Marked ``mongo``; skipped cleanly when docker, openssl, the mongo:8.0 image or
pymongo is missing. The rig (tests/store/mongo_rig.py) is a single-node replica
set with keyFile auth and ``requireTLS``, so every test here is authenticated
and TLS-encrypted for real.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

import pytest

pytest.importorskip("pymongo")

from pymongo import MongoClient  # noqa: E402
from pymongo.errors import DuplicateKeyError, OperationFailure, PyMongoError  # noqa: E402

from culture_rules.store.migrations import ensure_variables_collection  # noqa: E402
from culture_rules.store.mongo import ConfigError, MongoConfig, MongoStore  # noqa: E402
from culture_rules.store.port import VersionSkewError  # noqa: E402
from culture_rules.store.port import VARIABLES_COLLECTION, StoreError  # noqa: E402
from tests.store import mongo_rig  # noqa: E402
from tests.store.contract import StoragePortContract  # noqa: E402

pytestmark = pytest.mark.mongo

# The rig is started lazily, once per process, and torn down at interpreter exit, so
# ``make_store`` / ``open_peer`` also work when called standalone (the claims contract
# in tests/engine reuses them on another instance, outside this module's fixtures).
_state: dict = {"rig": None, "dir": None}
_opened: list[MongoStore] = []
_databases: list[str] = []


def get_rig() -> mongo_rig.Rig:
    if _state["rig"] is None:
        if not mongo_rig.docker_available():
            pytest.skip("docker, openssl or the mongo:8.0 image is not available")
        _state["dir"] = Path(tempfile.mkdtemp(prefix="culture-rules-mongo-"))
        try:
            _state["rig"] = mongo_rig.start(_state["dir"])
        except BaseException:
            shutil.rmtree(_state["dir"], ignore_errors=True)
            raise
        atexit.register(_shutdown)
    return _state["rig"]


def _release_stores() -> None:
    while _opened:
        _opened.pop().close()
    while _databases:
        mongo_rig.drop_database(_state["rig"], _databases.pop())


def _shutdown() -> None:
    try:
        _release_stores()
    finally:
        if _state["rig"] is not None:
            mongo_rig.stop(_state["rig"])
            _state["rig"] = None
        if _state["dir"] is not None:
            shutil.rmtree(_state["dir"], ignore_errors=True)


@pytest.fixture
def rig():
    return get_rig()


def _config(rig, database: str, **kw) -> MongoConfig:
    return MongoConfig(rig.app_uri(database), database=database, tls_ca_file=str(rig.ca_file), **kw)


class TestMongoStore(StoragePortContract):
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        _release_stores()

    def make_store(self, node_schema_version: str = "1.0") -> MongoStore:
        rig = get_rig()
        database = f"cr_{uuid.uuid4().hex[:12]}"
        mongo_rig.create_app_user(rig, database)
        _databases.append(database)
        store = MongoStore(_config(rig, database), node_schema_version=node_schema_version)
        _opened.append(store)
        return store

    def open_peer(self, store: MongoStore, node_schema_version: str) -> MongoStore:
        peer = MongoStore(store.config, node_schema_version=node_schema_version)
        _opened.append(peer)
        return peer


# --------------------------------------------------------------- mongo-specific


@pytest.fixture
def fresh(rig):
    database = f"cr_{uuid.uuid4().hex[:12]}"
    mongo_rig.create_app_user(rig, database)
    store = MongoStore(_config(rig, database))
    try:
        yield store
    finally:
        store.close()
        mongo_rig.drop_database(rig, database)


def test_unauthenticated_connect_fails(rig, fresh):
    anonymous = MongoClient(
        rig.uri(), tls=True, tlsCAFile=str(rig.ca_file), serverSelectionTimeoutMS=5000
    )
    try:
        with pytest.raises(OperationFailure):  # server requires authentication
            anonymous[fresh.config.database]["rules"].find_one()
    finally:
        anonymous.close()
    config = MongoConfig(rig.uri(), database="x", tls_ca_file=str(rig.ca_file))
    with pytest.raises(ConfigError, match="authentication"):  # the adapter refuses up front
        MongoStore(config)


def test_wrong_password_fails(rig, fresh):
    uri = rig.app_uri(fresh.config.database).replace(mongo_rig.APP_PASSWORD, "wrong")
    config = MongoConfig(uri, database=fresh.config.database, tls_ca_file=str(rig.ca_file))
    with pytest.raises(PyMongoError):
        MongoStore(config)


def test_plaintext_connection_is_impossible(rig, fresh):
    plain = MongoClient(rig.uri(), tls=False, serverSelectionTimeoutMS=1500)
    try:
        with pytest.raises(PyMongoError):
            plain.admin.command("ping")
    finally:
        plain.close()


def test_untrusted_ca_is_rejected(rig, fresh, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    mongo_rig._make_certs(other)  # a different CA: the server cert must not validate
    cfg = MongoConfig(
        rig.app_uri(fresh.config.database),
        database=fresh.config.database,
        tls_ca_file=str(other / "ca.pem"),
    )
    with pytest.raises(PyMongoError):
        MongoStore(cfg, server_selection_timeout_ms=2000)


def test_application_user_has_no_admin_role(rig, fresh):
    info = fresh.client[fresh.config.database].command("connectionStatus", showPrivileges=False)
    roles = info["authInfo"]["authenticatedUserRoles"]
    assert roles == [{"role": "readWrite", "db": fresh.config.database}]
    assert all(r["db"] != "admin" for r in roles)
    fresh.verify_least_privilege()  # passes
    visible = fresh.client.admin.command("listDatabases", nameOnly=True)["databases"]
    assert {d["name"] for d in visible} <= {fresh.config.database}  # only its own database
    with pytest.raises(OperationFailure):  # and indeed cannot do admin work
        fresh.client.admin.command("replSetGetStatus")
    with pytest.raises(OperationFailure):
        fresh.client.admin["system.users"].find_one()
    with pytest.raises(OperationFailure):
        fresh.client[fresh.config.database].command(
            "createUser", "evil", pwd="x", roles=[{"role": "root", "db": "admin"}]
        )


def test_adapter_refuses_an_admin_privileged_user(rig):
    admin_cfg = MongoConfig(
        rig.uri(mongo_rig.ADMIN_USER, mongo_rig.ADMIN_PASSWORD, authSource="admin"),
        database="cr_admin_probe",
        tls_ca_file=str(rig.ca_file),
    )
    with pytest.raises(ConfigError, match="admin"):
        MongoStore(admin_cfg)


def test_writes_use_majority_write_concern(fresh):
    assert fresh.client.write_concern.document == {"w": "majority"}
    coll = fresh._collection("rules")
    assert coll.write_concern.document == {"w": "majority"}
    assert coll.read_concern.level == "majority"


def test_majority_is_sent_on_the_wire(rig, fresh):
    from pymongo import monitoring

    seen: list[dict] = []

    class Listener(monitoring.CommandListener):
        def started(self, event):
            if event.command_name in {"insert", "update", "delete", "findAndModify"}:
                seen.append(dict(event.command.get("writeConcern", {})))

        def succeeded(self, event):
            pass

        def failed(self, event):
            pass

    listener = Listener()
    spy = MongoStore(
        _config(rig, fresh.config.database), event_listeners=[listener], node_schema_version="1.0"
    )
    try:
        spy.insert("rules", {"id": "w1"})
        spy.put("rules", {"id": "w1", "n": 1})
        spy.update_if("rules", "w1", {"n": 1}, {"n": 2})
        spy.delete("rules", "w1")
    finally:
        spy.close()
    assert seen
    assert all(wc.get("w") == "majority" for wc in seen)


def test_change_stream_post_images_are_enabled(fresh):
    fresh.put("rules", {"id": "a"})
    options = fresh.client[fresh.config.database].list_collections(filter={"name": "rules"})
    (info,) = list(options)
    assert info["options"]["changeStreamPreAndPostImages"] == {"enabled": True}


def test_resume_tokens_are_persisted_per_consumer_in_the_store(fresh):
    start = fresh.head("events")
    fresh.put("events", {"id": "e1"})
    fresh.put("events", {"id": "e2"})
    fresh.put("events", {"id": "e3"})
    c1, c2, c3 = list(fresh.changes("events", start))
    fresh.save_cursor("alpha", "events", c1.token)
    fresh.save_cursor("beta", "events", c2.token)
    doc = fresh.get("_cursors", "alpha/events")
    assert doc["token"] == c1.token
    assert doc["consumer"] == "alpha"
    # a different process/host picks the cursor up from the database and resumes
    other = MongoStore(fresh.config)
    try:
        assert [c.id for c in other.changes("events", other.load_cursor("alpha", "events"))] == [
            "e2",
            "e3",
        ]
        assert [c.id for c in other.changes("events", other.load_cursor("beta", "events"))] == [
            "e3"
        ]
        assert c3.token != c2.token
    finally:
        other.close()
    fresh.save_cursor("alpha", "events", c3.token)  # advancing replaces, never duplicates
    assert len(fresh.find("_cursors", {"consumer": "alpha"})) == 1


def test_resume_survives_a_new_client(fresh):
    start = fresh.head("events")
    fresh.put("events", {"id": "one"})
    resume = next(iter(fresh.changes("events", start))).token
    fresh.put("events", {"id": "two"})
    other = MongoStore(fresh.config)
    try:
        assert [c.id for c in other.changes("events", resume)] == ["two"]
    finally:
        other.close()


def test_invalid_resume_token_is_a_value_error(fresh):
    with pytest.raises(ValueError):
        list(fresh.changes("events", ""))
    with pytest.raises(StoreError):  # the server rejects the resume token
        list(fresh.changes("events", "not-a-real-token"))


def test_engine_writes_no_state_to_local_files(rig, tmp_path, monkeypatch):
    workdir = tmp_path / "data"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    for var in ("HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "TMPDIR"):
        monkeypatch.setenv(var, str(workdir))
    database = f"cr_{uuid.uuid4().hex[:12]}"
    mongo_rig.create_app_user(rig, database)
    store = MongoStore(_config(rig, database))
    try:
        start = store.head("rules")
        store.insert("rules", {"id": "a", "n": 1})
        store.put("rules", {"id": "b"})
        assert store.update_if("rules", "a", {"n": 1}, {"n": 2}).won
        with store.transaction() as tx:
            tx.put("rules", {"id": "c"})
        assert len(list(store.changes("rules", start))) == 4
        store.save_cursor("consumer", "rules", store.head("rules"))
        store.delete("rules", "a")
    finally:
        store.close()
        mongo_rig.drop_database(rig, database)
    assert os.listdir(workdir) == []  # no data dir, cache, spool or state file appeared


def test_concurrent_cas_across_independent_clients(fresh):
    stores = [MongoStore(fresh.config) for _ in range(4)]
    try:
        fresh.insert("claims", {"id": "k", "owner": None})
        barrier = threading.Barrier(12)
        wins: list[int] = []

        def contend(i: int) -> None:
            barrier.wait()
            if stores[i % 4].update_if("claims", "k", {"owner": None}, {"owner": i}).won:
                wins.append(i)

        threads = [threading.Thread(target=contend, args=(i,)) for i in range(12)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(wins) == 1
        assert fresh.get("claims", "k")["owner"] == wins[0]
    finally:
        for s in stores:
            s.close()


# ------------------------------------------------------------------- variables

from tests.store.test_variable import VariableContract  # noqa: E402


class TestVariableMongoContract(VariableContract):
    @pytest.fixture(autouse=True)
    def _cleanup(self):
        yield
        _release_stores()

    def make_store(self) -> MongoStore:
        rig = get_rig()
        database = f"cr_{uuid.uuid4().hex[:12]}"
        mongo_rig.create_app_user(rig, database)
        _databases.append(database)
        store = MongoStore(_config(rig, database))
        _opened.append(store)
        return store


def test_ensure_variables_collection_creates_unique_index(fresh):
    store = fresh
    ensure_variables_collection(store)
    db = store.client[store.config.database]
    assert VARIABLES_COLLECTION in db.list_collection_names()
    indexes = {i["name"]: i for i in db[VARIABLES_COLLECTION].list_indexes()}
    assert indexes["name_unique"]["unique"] is True
    assert dict(indexes["name_unique"]["key"]) == {"name": 1}
    # Idempotent: running it again is a no-op.
    ensure_variables_collection(store)
    # The collection is usable straight after the migration.
    doc = store.put_variable("a", 1, updated_by="me")
    assert doc["version"] == 1
    assert store.get_variable("a")["value"] == 1


def test_variables_index_enforces_unique_names(fresh):
    """The index (not just the _id) keeps one document per variable name."""
    store = fresh
    ensure_variables_collection(store)
    coll = store.client[store.config.database][VARIABLES_COLLECTION]
    coll.insert_one({"name": "dup"})
    with pytest.raises(DuplicateKeyError):
        coll.insert_one({"name": "dup"})


# -------------------------------------------------------------------
# T1: concurrent version allocation


def test_concurrent_variable_puts_yield_no_duplicates_or_gaps(fresh):
    """5 threads x 5 puts on one variable name → versions {1..25} exactly (T1)."""
    ensure_variables_collection(fresh)
    threads, per_thread = 5, 5
    stores = [MongoStore(fresh.config) for _ in range(threads)]
    barrier = threading.Barrier(threads)
    results: list[tuple[int, int, str]] = []  # (thread_id, version, value)
    lock = threading.Lock()

    def put_loop(tid: int) -> None:
        barrier.wait()
        for i in range(per_thread):
            value = f"v{tid}-{i}"
            doc = stores[tid].put_variable("counter", value, updated_by=f"t{tid}")
            with lock:
                results.append((tid, doc["version"], value))

    tg = [threading.Thread(target=put_loop, args=(i,)) for i in range(threads)]
    for t in tg:
        t.start()
    for t in tg:
        t.join()

    versions = {v for _, v, _ in results}
    expected = set(range(1, threads * per_thread + 1))
    assert versions == expected, f"expected {expected}, got {versions}"
    # Every returned version is readable back with the value that thread wrote.
    for tid, ver, expected_value in results:
        doc = stores[tid].get_variable_version("counter", ver)
        assert doc is not None, f"version {ver} not found for tid {tid}"
        assert (
            doc["value"] == expected_value
        ), f"tid={tid} ver={ver}: expected {expected_value!r}, got {doc['value']!r}"
    for s in stores:
        s.close()


# ------------------------------------------------------------------- T2: envelope stamping


def test_variable_doc_carries_envelope(fresh):
    """put_variable stamps schema_version + updated_at (T2)."""
    ensure_variables_collection(fresh)
    doc = fresh.put_variable("a", 1, updated_by="me")
    assert "schema_version" in doc
    assert "updated_at" in doc


def test_variable_doc_carries_envelope_in_mongo(fresh):
    """The raw mongo doc also has the envelope (T2)."""
    ensure_variables_collection(fresh)
    fresh.put_variable("a", 1, updated_by="me")
    raw = fresh.client[fresh.config.database][VARIABLES_COLLECTION].find_one({"_id": "a"})
    assert raw is not None
    assert "schema_version" in raw
    assert "updated_at" in raw


def test_variable_with_newer_schema_raises_version_skew(fresh):
    """A variable doc with a newer schema_version raises VersionSkewError on write (T2)."""
    ensure_variables_collection(fresh)
    # Write a doc with a newer schema from a higher-versioned store.
    high = MongoStore(fresh.config, node_schema_version="2.0")
    try:
        high.put_variable("skew", 1, updated_by="me")
    finally:
        high.close()
    # Now a lower-versioned store should refuse to write that doc.
    store = MongoStore(fresh.config, node_schema_version="1.0")
    try:
        with pytest.raises(VersionSkewError):
            store.put_variable("skew", 2, updated_by="me")
    finally:
        store.close()


def test_variable_doc_without_counter_still_accepts_a_put(fresh):
    """A doc with history but no latest_version takes the next version (Codex t3 r2)."""
    ensure_variables_collection(fresh)
    fresh.put_variable("legacy", 1, updated_by="me")
    fresh.put_variable("legacy", 2, updated_by="me")
    fresh._collection("variables").update_one({"_id": "legacy"}, {"$unset": {"latest_version": ""}})
    view = fresh.put_variable("legacy", 3, updated_by="me")
    assert view["version"] == 3
    assert fresh.get_variable_version("legacy", 2)["value"] == 2
    assert fresh.get_variable("legacy")["value"] == 3


def test_find_events_is_served_by_the_type_received_index(fresh):
    from culture_rules.store.mongo import EVENTS_TYPE_RECEIVED_INDEX

    for n in range(30):
        kind = "a.done" if n % 3 == 0 else "noise"
        fresh.insert(
            "events",
            {
                "id": f"e{n:02d}",
                "envelope": {"id": f"e{n:02d}", "type": kind},
                "received_at": f"2026-10-07T12:00:{n:02d}.000000+00:00",
            },
        )
    page = fresh.find_events(
        types=("a.done",), after=("2026-10-07T12:00:03.000000+00:00", "e03"), until="~", limit=3
    )
    assert [e["id"] for e in page] == ["e06", "e09", "e12"]
    names = [ix["name"] for ix in fresh.client[fresh.config.database]["events"].list_indexes()]
    assert EVENTS_TYPE_RECEIVED_INDEX in names
    query = {
        "envelope.type": {"$in": ["a.done"]},
        "received_at": {"$gte": "2026-10-07T12:00:03.000000+00:00", "$lte": "~"},
        "$or": [
            {"received_at": {"$gt": "2026-10-07T12:00:03.000000+00:00"}},
            {"_id": {"$gt": "e03"}},
        ],
    }
    plan = (
        fresh.client[fresh.config.database]["events"]
        .find(query)
        .sort([("received_at", 1), ("_id", 1)])
        .limit(3)
        .explain()
    )
    stats = plan["executionStats"]
    assert EVENTS_TYPE_RECEIVED_INDEX in str(plan["queryPlanner"]["winningPlan"])
    assert stats["totalDocsExamined"] <= 4  # never the noise or the events before the cursor


# ------------------------------------------------------------------- characterization
# (the Sonar S3776 split of MongoStore.put_variable: its CAS shape and contention bound)


def test_put_variable_cas_expects_the_versions_as_read_and_bounds_contention(fresh):
    ensure_variables_collection(fresh)
    fresh.put_variable("v", 1, updated_by="me")
    real = fresh._update_if
    seen = []

    def lose(tx, collection, id, expected, changes, *, upsert=False):
        seen.append((dict(expected), dict(changes), upsert))
        return type("R", (), {"won": False})()

    fresh._update_if = lose
    with pytest.raises(StoreError) as exc:
        fresh.put_variable("v", 2, updated_by="you", description="d")
    fresh._update_if = real
    assert str(exc.value) == "put_variable 'v': too much contention after 50 CAS attempts"
    assert len(seen) == 50
    expected, changes, upsert = seen[0]
    assert upsert is False
    assert expected["latest_version"] == 1 and [v["value"] for v in expected["versions"]] == [1]
    assert [k for k in changes] == [
        "name",
        "value",
        "version",
        "updated_by",
        "updated_at",
        "description",
        "versions",
        "latest_version",
    ]
    assert (changes["version"], changes["latest_version"], changes["value"]) == (2, 2, 2)
    assert [v["version"] for v in changes["versions"]] == [1, 2]
    assert changes["versions"][-1]["description"] == "d"
    assert fresh.get_variable("v")["value"] == 1


def test_put_variable_first_write_upserts_and_expects_nothing(fresh):
    ensure_variables_collection(fresh)
    real = fresh._update_if
    seen = []

    def spy(tx, collection, id, expected, changes, *, upsert=False):
        seen.append((dict(expected), upsert))
        return real(tx, collection, id, expected, changes, upsert=upsert)

    fresh._update_if = spy
    view = fresh.put_variable("n", "x", updated_by="me", expected_version=0)
    fresh._update_if = real
    assert seen == [({"versions": None, "latest_version": None}, True)]
    assert view["version"] == 1


def test_put_variable_expected_version_conflict(fresh):
    from culture_rules.store.port import VariableVersionConflict

    ensure_variables_collection(fresh)
    fresh.put_variable("c", 1, updated_by="me")
    with pytest.raises(VariableVersionConflict) as exc:
        fresh.put_variable("c", 2, updated_by="me", expected_version=0)
    assert str(exc.value) == "variable 'c' is at version 1, not 0"
