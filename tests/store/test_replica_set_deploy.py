"""t36: replica-set deploy artifacts (static checks) and the chaos check (docker, ``mongo``).

The static tests read ``deploy/replica-set/`` and ``docs/operations/replica-set.md``. The chaos
test brings up four local mongod containers (spark, thor, orin, spark2) from the *same*
``rs-config.json`` template the operator uses, with a keyFile and TLS, and proves that with
spark2 stopped plus any one other member stopped a primary still takes majority writes.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy" / "replica-set"
DOC = ROOT / "docs" / "operations" / "replica-set.md"
HOSTS = ("spark", "thor", "orin", "spark2")


def _template() -> dict:
    return json.loads((DEPLOY / "rs-config.json").read_text())


def test_deploy_artifacts_exist() -> None:
    for name in ("compose.yaml", "replica-set.env.example", "gen-certs.sh", "rs-config.json"):
        assert (DEPLOY / name).is_file(), name
    assert (DEPLOY / "mongod.service").is_file()
    assert DOC.is_file()


def test_default_port_is_not_27017() -> None:
    texts = [p.read_text() for p in DEPLOY.iterdir() if p.is_file()] + [DOC.read_text()]
    env = (DEPLOY / "replica-set.env.example").read_text()
    assert "MONGO_PORT=27018" in env
    # 27017 may only be mentioned as the port we avoid, never bound
    for text in texts:
        for line in text.splitlines():
            if "27017" in line:
                assert any(w in line.lower() for w in ("not", "never", "avoid", "default")), line


def test_compose_and_unit_enforce_keyfile_and_tls() -> None:
    for name in ("compose.yaml", "mongod.service"):
        text = (DEPLOY / name).read_text()
        assert "--keyFile" in text, name
        assert "requireTLS" in text, name
        assert "--tlsCertificateKeyFile" in text, name
        assert "--replSet" in text, name
        assert "--port" in text, name


def test_compose_runs_mongod_as_the_mongodb_user_not_root() -> None:
    text = (DEPLOY / "compose.yaml").read_text()
    assert "exec gosu mongodb mongod" in text
    # key and TLS material must be readable by that user before the drop
    assert "chown -R mongodb:mongodb" in text and "/data/db" in text
    assert "exec mongod" not in text


def test_rs_config_topology_has_three_always_up_voters() -> None:
    cfg = _template()
    members = {m["tags"]["host"]: m for m in cfg["members"]}
    assert set(members) == set(HOSTS)
    voters = [h for h, m in members.items() if m.get("votes", 1) == 1]
    assert sorted(voters) == ["orin", "spark", "thor"]
    # a non-voting member must not be electable
    assert members["spark2"]["votes"] == 0 and members["spark2"]["priority"] == 0
    # the third voter is a data-bearing member, not an arbiter (w:majority needs data nodes)
    assert not any(m.get("arbiterOnly") for m in cfg["members"])


def test_doc_records_choice_checklist_and_chaos() -> None:
    text = DOC.read_text()
    for needle in ("orin", "arbiter", "Operator checklist", "Chaos check", "27018", "keyFile"):
        assert needle in text, needle
    assert "x509" in text and "TLS" in text


# -- chaos check against four local mongod containers ---------------------------------------

pytestmark_mongo = pytest.mark.mongo


@pytest.fixture(scope="module")
def cluster():
    pytest.importorskip("pymongo")
    from tests.store import mongo_rig

    if not mongo_rig.docker_available():
        pytest.skip("docker, openssl or the mongo:8.0 image is unavailable")
    from tests.store import replica_set_rig

    workdir = Path(tempfile.mkdtemp(prefix="cr-rs-"))
    rs = replica_set_rig.start(workdir, _template())
    try:
        yield rs
    finally:
        replica_set_rig.stop(rs)
        shutil.rmtree(workdir, ignore_errors=True)


@pytest.mark.mongo
@pytest.mark.parametrize("other", ["spark", "thor", "orin"])
def test_primary_survives_spark2_plus_one_member_down(cluster, other: str) -> None:
    from tests.store import replica_set_rig as rig

    cluster.stop("spark2")
    cluster.stop(other)
    try:
        doc_id = f"chaos-{other}-{uuid.uuid4().hex[:6]}"
        survivors = [h for h in HOSTS if h not in ("spark2", other)]
        rig.write_majority(cluster, survivors, doc_id)  # raises unless a primary acked w:majority
        assert rig.read_back(cluster, survivors, doc_id)
    finally:
        cluster.start("spark2")
        cluster.start(other)
        rig.wait_all_healthy(cluster)


@pytest.mark.mongo
def test_losing_two_voters_blocks_majority_writes(cluster) -> None:
    """Negative control: the topology is what keeps writes up, not luck."""
    from tests.store import replica_set_rig as rig

    cluster.stop("spark")
    cluster.stop("thor")
    try:
        with pytest.raises(Exception):  # noqa: B017 - no primary / majority timeout
            rig.write_majority(cluster, ["orin", "spark2"], "should-fail", timeout=8)
    finally:
        cluster.start("spark")
        cluster.start("thor")
        rig.wait_all_healthy(cluster)
        time.sleep(0)
