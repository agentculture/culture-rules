"""Four local mongod containers forming the t36 replica set (spark, thor, orin, spark2).

Built from deploy/replica-set/rs-config.json with each ``@host@`` placeholder replaced by
``127.0.0.1:<random port>``. Same flags as deploy/replica-set/compose.yaml: keyFile + requireTLS.
Host networking, random ports (never 27017), everything removed on teardown.
"""

from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from tests.store import mongo_rig

ADMIN_USER = mongo_rig.ADMIN_USER
ADMIN_PASSWORD = mongo_rig.ADMIN_PASSWORD


@dataclass
class ReplicaSet:
    workdir: Path
    name: str
    ports: dict[str, int]
    containers: dict[str, str] = field(default_factory=dict)

    def stop(self, host: str) -> None:
        mongo_rig._run("docker", "stop", "-t", "2", self.containers[host], timeout=60)

    def start(self, host: str) -> None:
        mongo_rig._run("docker", "start", self.containers[host], timeout=60)


def _mongod_script(port: int, set_name: str) -> str:
    return (
        "cp /certs/keyfile /tmp/keyfile && chmod 400 /tmp/keyfile && mkdir -p /tmp/db && "
        f"exec mongod --replSet {set_name} --bind_ip 127.0.0.1 --port {port} "
        "--keyFile /tmp/keyfile --tlsMode requireTLS --tlsCertificateKeyFile /certs/server.pem "
        "--tlsCAFile /certs/ca.pem --tlsAllowConnectionsWithoutCertificates "
        "--dbpath /tmp/db --wiredTigerCacheSizeGB 0.25"
    )


def _client(port: int, **kw):
    from pymongo import MongoClient

    return MongoClient(
        "127.0.0.1",
        port,
        tls=True,
        tlsCAFile=str(kw.pop("ca")),
        directConnection=True,
        serverSelectionTimeoutMS=kw.pop("timeout_ms", 3000),
        **kw,
    )


def start(workdir: Path, template: dict) -> ReplicaSet:
    from pymongo.errors import PyMongoError

    mongo_rig._make_certs(workdir)
    hosts = [m["tags"]["host"] for m in template["members"]]
    ports = {h: mongo_rig.free_port() for h in hosts}
    assert 27017 not in ports.values()
    rs = ReplicaSet(workdir, template["_id"], ports)
    try:
        for host in hosts:
            name = f"culture-rules-rs-{host}-{uuid.uuid4().hex[:6]}"
            mongo_rig._run(
                *("docker", "run", "-d", "--network", "host", "--name", name),
                *("-v", f"{workdir}:/certs:ro", "--entrypoint", "bash", mongo_rig.IMAGE),
                *("-c", _mongod_script(ports[host], template["_id"])),
            )
            rs.containers[host] = name
        config = copy.deepcopy(template)
        for member in config["members"]:
            host = member["tags"]["host"]
            member["host"] = member["host"].replace(f"@{host}@", f"127.0.0.1:{ports[host]}")
        first = _client(ports["spark"], ca=workdir / "ca.pem")
        deadline = time.monotonic() + 90
        while True:  # localhost exception: no auth yet
            try:
                first.admin.command("replSetInitiate", config)
                break
            except PyMongoError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        while not first.admin.command("hello").get("isWritablePrimary"):
            if time.monotonic() > deadline:
                raise RuntimeError("no primary elected")
            time.sleep(0.3)
        first.admin.command(
            "createUser",
            ADMIN_USER,
            pwd=ADMIN_PASSWORD,
            roles=[{"role": "root", "db": "admin"}],
            writeConcern={"w": "majority"},
        )
        first.close()
        wait_all_healthy(rs)
    except BaseException:
        stop(rs)
        raise
    return rs


def stop(rs: ReplicaSet) -> None:
    for name in rs.containers.values():
        mongo_rig._run("docker", "rm", "-f", name, check=False, timeout=60)


def _uri(rs: ReplicaSet, hosts: list[str]) -> str:
    seeds = ",".join(f"127.0.0.1:{rs.ports[h]}" for h in hosts)
    return f"mongodb://{ADMIN_USER}:{ADMIN_PASSWORD}@{seeds}/?replicaSet={rs.name}&authSource=admin"


def write_majority(rs: ReplicaSet, hosts: list[str], doc_id: str, timeout: float = 40) -> None:
    """Insert one document through the live members with w:majority; raise unless acked."""
    from pymongo import MongoClient
    from pymongo.write_concern import WriteConcern

    client = MongoClient(
        _uri(rs, hosts),
        tls=True,
        tlsCAFile=str(rs.workdir / "ca.pem"),
        serverSelectionTimeoutMS=int(timeout * 1000),
    )
    try:
        coll = client["chaos"].get_collection(
            "probe", write_concern=WriteConcern(w="majority", wtimeout=int(timeout * 1000))
        )
        coll.insert_one({"_id": doc_id})
    finally:
        client.close()


def read_back(rs: ReplicaSet, hosts: list[str], doc_id: str) -> bool:
    from pymongo import MongoClient
    from pymongo.read_concern import ReadConcern

    client = MongoClient(
        _uri(rs, hosts),
        tls=True,
        tlsCAFile=str(rs.workdir / "ca.pem"),
        serverSelectionTimeoutMS=20000,
    )
    try:
        coll = client["chaos"].get_collection("probe", read_concern=ReadConcern("majority"))
        return coll.find_one({"_id": doc_id}) is not None
    finally:
        client.close()


def wait_all_healthy(rs: ReplicaSet, timeout: float = 90) -> None:
    """Block until every member is PRIMARY or SECONDARY (and a primary exists)."""
    from pymongo.errors import PyMongoError

    deadline = time.monotonic() + timeout
    while True:
        try:
            client = _client(
                rs.ports["orin"],
                ca=rs.workdir / "ca.pem",
                timeout_ms=2000,
                username=ADMIN_USER,
                password=ADMIN_PASSWORD,
                authSource="admin",
            )
            try:
                st = client.admin.command("replSetGetStatus")
                states = [m["stateStr"] for m in st["members"]]
            finally:
                client.close()
            if all(s in ("PRIMARY", "SECONDARY") for s in states) and "PRIMARY" in states:
                return
        except PyMongoError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError("replica set did not settle")
        time.sleep(1)
