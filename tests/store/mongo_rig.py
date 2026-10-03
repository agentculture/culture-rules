"""Disposable MongoDB replica-set rig for the ``mongo`` integration tests.

Starts one ``mongo:8.0`` container: a single-node replica set with a keyFile
(so authentication is enforced), ``--tlsMode requireTLS`` with a throwaway CA
generated here by the ``openssl`` CLI, on a random free host port (never 27017).
It uses host networking so the replica-set member address is the address the
client dials. Everything is removed on teardown. Nothing here talks to a real
external service.
"""

from __future__ import annotations

import shutil
import socket
import subprocess  # nosec B404
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

IMAGE = "mongo:8.0"
REPLICA_SET = "rs0"
ADMIN_USER = "rig-admin"
ADMIN_PASSWORD = "rig-admin-pw"  # noqa: S105 - throwaway container credential
APP_USER = "rig-app"
APP_PASSWORD = "rig-app-pw"  # noqa: S105 - throwaway container credential


class RigUnavailable(Exception):
    """Docker (or openssl, or the image) is not usable here; tests should skip."""


@dataclass
class Rig:
    container: str
    port: int
    ca_file: Path
    workdir: Path

    def uri(self, user: str | None = None, password: str | None = None, **opts: str) -> str:
        auth = f"{user}:{password}@" if user else ""
        query = "&".join([f"replicaSet={REPLICA_SET}", *(f"{k}={v}" for k, v in opts.items())])
        return f"mongodb://{auth}127.0.0.1:{self.port}/?{query}"

    def app_uri(self, database: str) -> str:
        return (
            f"mongodb://{APP_USER}:{APP_PASSWORD}@127.0.0.1:{self.port}/{database}"
            f"?replicaSet={REPLICA_SET}&authSource={database}"
        )


def _run(*args: str, check: bool = True, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603
        list(args), capture_output=True, text=True, check=check, timeout=timeout
    )


def docker_available() -> bool:
    if shutil.which("docker") is None or shutil.which("openssl") is None:
        return False
    try:
        if _run("docker", "info", check=False, timeout=20).returncode != 0:
            return False
        return bool(_run("docker", "images", "-q", IMAGE, check=False).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _make_certs(directory: Path) -> None:
    ca_key, ca_pem = directory / "ca.key", directory / "ca.pem"
    srv_key, srv_csr, srv_crt = directory / "s.key", directory / "s.csr", directory / "s.crt"
    ext = directory / "ext.cnf"
    ext.write_text("subjectAltName=DNS:localhost,IP:127.0.0.1\n")
    _run(
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "2",
        "-subj",
        "/CN=culture-rules-test-ca",
        "-keyout",
        str(ca_key),
        "-out",
        str(ca_pem),
    )
    _run(
        "openssl",
        "req",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-subj",
        "/CN=localhost",
        "-keyout",
        str(srv_key),
        "-out",
        str(srv_csr),
    )
    _run(
        "openssl",
        "x509",
        "-req",
        "-in",
        str(srv_csr),
        "-CA",
        str(ca_pem),
        "-CAkey",
        str(ca_key),
        "-CAcreateserial",
        "-days",
        "2",
        "-extfile",
        str(ext),
        "-out",
        str(srv_crt),
    )
    (directory / "server.pem").write_text(srv_crt.read_text() + srv_key.read_text())
    key = _run("openssl", "rand", "-base64", "256").stdout
    (directory / "keyfile").write_text(key)
    for path in directory.iterdir():
        path.chmod(0o644)


def start(workdir: Path) -> Rig:
    """Start the container and bootstrap replica set, admin user and app user."""
    from pymongo import MongoClient  # lazy: only integration tests need it
    from pymongo.errors import PyMongoError

    _make_certs(workdir)
    port = free_port()
    name = f"culture-rules-mongo-test-{uuid.uuid4().hex[:8]}"
    script = (
        "cp /certs/keyfile /tmp/keyfile && chmod 400 /tmp/keyfile && "
        f"exec mongod --replSet {REPLICA_SET} --bind_ip 127.0.0.1 --port {port} "
        "--keyFile /tmp/keyfile --tlsMode requireTLS --tlsCertificateKeyFile /certs/server.pem "
        "--tlsCAFile /certs/ca.pem --tlsAllowConnectionsWithoutCertificates "
        "--dbpath /tmp/db --wiredTigerCacheSizeGB 0.25"
    )
    _run(
        "docker",
        "run",
        "-d",
        "--rm",
        "--network",
        "host",
        "--name",
        name,
        "-v",
        f"{workdir}:/certs:ro",
        "--entrypoint",
        "bash",
        IMAGE,
        "-c",
        "mkdir -p /tmp/db && " + script,
    )
    rig = Rig(name, port, workdir / "ca.pem", workdir)
    try:
        _bootstrap(rig, MongoClient, PyMongoError)
    except BaseException:
        stop(rig)
        raise
    return rig


def _bootstrap(rig: Rig, mongo_client, pymongo_error) -> None:
    kwargs = {"tls": True, "tlsCAFile": str(rig.ca_file), "directConnection": True}
    deadline = time.monotonic() + 60
    client = mongo_client("127.0.0.1", rig.port, serverSelectionTimeoutMS=2000, **kwargs)
    while True:  # wait for mongod, then initiate (localhost exception: no auth yet)
        try:
            client.admin.command(
                "replSetInitiate",
                {"_id": REPLICA_SET, "members": [{"_id": 0, "host": f"127.0.0.1:{rig.port}"}]},
            )
            break
        except pymongo_error:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.5)
    while not client.admin.command("hello").get("isWritablePrimary"):
        if time.monotonic() > deadline:
            raise RuntimeError("replica set never elected a primary")
        time.sleep(0.3)
    client.admin.command(
        "createUser",
        ADMIN_USER,
        pwd=ADMIN_PASSWORD,
        roles=[{"role": "root", "db": "admin"}],
        writeConcern={"w": "majority"},
    )
    client.close()


def create_app_user(rig: Rig, database: str) -> None:
    """Create ``rig-app`` in ``database`` with readWrite there only (no admin-database role)."""
    from pymongo import MongoClient

    admin = MongoClient(
        rig.uri(ADMIN_USER, ADMIN_PASSWORD, authSource="admin"),
        tls=True,
        tlsCAFile=str(rig.ca_file),
        serverSelectionTimeoutMS=10000,
    )
    try:
        db = admin[database]
        db.command(
            "createUser",
            APP_USER,
            pwd=APP_PASSWORD,
            roles=[{"role": "readWrite", "db": database}],
            writeConcern={"w": "majority"},
        )
    finally:
        admin.close()


def stop(rig: Rig) -> None:
    _run("docker", "rm", "-f", rig.container, check=False, timeout=60)


def drop_database(rig: Rig, database: str) -> None:
    """Drop a per-test database (and with it the app user created in it)."""
    from pymongo import MongoClient

    admin = MongoClient(
        rig.uri(ADMIN_USER, ADMIN_PASSWORD, authSource="admin"),
        tls=True,
        tlsCAFile=str(rig.ca_file),
        serverSelectionTimeoutMS=10000,
    )
    try:
        admin.drop_database(database)
    finally:
        admin.close()
