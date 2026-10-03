"""Restore a backup into an empty, authenticated MongoDB replica set (docker rig)."""

from __future__ import annotations

import pytest

pytest.importorskip("pymongo")
boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

import tests.store.test_mongo as mongo_tests  # noqa: E402
from culture_rules.ops.backup import Backup, BackupConfig  # noqa: E402
from culture_rules.store.memory import MemoryStore  # noqa: E402
from tests.ops.test_backup import BUCKET, REGION, Clock, dump, seed  # noqa: E402

pytestmark = pytest.mark.mongo


def test_restore_into_empty_replica_set_reproduces_everything():
    mongo = mongo_tests.TestMongoStore().make_store()
    try:
        source = MemoryStore()
        seed(source)
        clock = Clock()
        with moto.mock_aws():
            s3 = boto3.client("s3", region_name=REGION)
            s3.create_bucket(Bucket=BUCKET)
            s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
            backup = Backup(
                BackupConfig(bucket=BUCKET, region=REGION), source, client=s3, clock=clock
            )
            backup.snapshot()
            source.put("runs", {"id": "run3", "state": "new"})
            clock.advance(hours=1)
            backup.increment()
            report = backup.restore(mongo)
        assert report.increments == 1
        assert dump(mongo) == dump(source)
    finally:
        mongo_tests._release_stores()
