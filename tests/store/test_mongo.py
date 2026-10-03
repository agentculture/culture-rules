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
from pymongo.errors import OperationFailure, PyMongoError  # noqa: E402

from culture_rules.store.mongo import ConfigError, MongoConfig, MongoStore  # noqa: E402
from culture_rules.store.port import StoreError  # noqa: E402
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
