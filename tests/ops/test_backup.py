"""Backup: consistent snapshots, hourly run-history increments, encryption, restore."""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from culture_rules.ops.backup import (  # noqa: E402
    Backup,
    BackupConfig,
    BackupConfigError,
    BackupError,
    resolve_secret,
)
from culture_rules.store.memory import MemoryStore  # noqa: E402

BUCKET = "cr-backup-test"
REGION = "us-east-1"
T0 = datetime(2026, 10, 3, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


@pytest.fixture
def s3():
    with moto.mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        yield client


@pytest.fixture
def clock():
    return Clock()


def make_backup(s3, store, clock, **cfg) -> Backup:
    config = BackupConfig(bucket=BUCKET, region=REGION, **cfg)
    return Backup(config, store, client=s3, clock=clock)


def seed(store: MemoryStore) -> None:
    for i in range(3):
        store.put("rules", {"id": f"r{i}", "name": f"rule {i}"})
    store.put("workflows", {"id": "w1", "steps": [{"a": 1}]})
    store.put("actors", {"id": "a1", "kind": "agent"})
    store.put("runs", {"id": "run1", "state": "done"})
    store.put("runs", {"id": "run2", "state": "waiting"})


def dump(store, collections=("rules", "workflows", "actors", "runs", "audit", "machines")):
    out = {}
    for c in collections:
        out[c] = {d["id"]: {k: v for k, v in d.items() if k != "updated_at"} for d in store.find(c)}
    return out


# ------------------------------------------------------------------ config


def test_config_from_env_only():
    cfg = BackupConfig.from_env(
        {
            "CULTURE_RULES_BACKUP_BUCKET": "b",
            "CULTURE_RULES_BACKUP_REGION": "eu-west-1",
            "CULTURE_RULES_BACKUP_SSE": "aws:kms",
            "CULTURE_RULES_BACKUP_KMS_KEY_ID": "alias/x",
        }
    )
    assert (cfg.bucket, cfg.region, cfg.sse, cfg.kms_key_id) == (
        "b",
        "eu-west-1",
        "aws:kms",
        "alias/x",
    )


def test_config_requires_bucket_and_region():
    with pytest.raises(BackupConfigError):
        BackupConfig.from_env({"CULTURE_RULES_BACKUP_REGION": "r"})
    with pytest.raises(BackupConfigError):
        BackupConfig.from_env({"CULTURE_RULES_BACKUP_BUCKET": "b"})


def test_config_rejects_unencrypted_mode():
    with pytest.raises(BackupConfigError):
        BackupConfig(bucket="b", region="r", sse="none")


def test_resolve_secret_passes_plain_and_uses_shushu_runner():
    assert resolve_secret("plain") == "plain"
    assert resolve_secret("shushu:aws/key", runner=lambda name: f"got-{name}") == "got-aws/key"


# ---------------------------------------------------------------- snapshot


def test_snapshot_is_server_side_encrypted(s3, clock):
    store = MemoryStore()
    seed(store)
    rec = make_backup(s3, store, clock).snapshot()
    head = s3.head_object(Bucket=BUCKET, Key=rec.key)
    assert head["ServerSideEncryption"] == "AES256"
    assert rec.counts["rules"] == 3 and rec.counts["runs"] == 2


def test_kms_sse_passes_key_id(s3, clock):
    store = MemoryStore()
    seed(store)
    rec = make_backup(s3, store, clock, sse="aws:kms").snapshot()
    assert s3.head_object(Bucket=BUCKET, Key=rec.key)["ServerSideEncryption"] == "aws:kms"


def test_unversioned_bucket_is_refused(clock):
    with moto.mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        with pytest.raises(BackupError, match="versioning"):
            make_backup(client, MemoryStore(), clock).snapshot()


def test_repeated_snapshots_do_not_overwrite(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    k1 = b.snapshot().key
    clock.advance(hours=24)
    k2 = b.snapshot().key
    assert k1 != k2
    assert len(b.list_backups()) == 2


def test_snapshot_is_consistent_under_concurrent_writes(s3, clock):
    """Writers hammer the store while snapshotting; the restored copy must be a real state."""
    store = MemoryStore()
    for i in range(50):
        store.put("rules", {"id": f"r{i}", "gen": 0})
    stop = threading.Event()

    def writer():
        gen = 0
        while not stop.is_set():
            gen += 1
            with store.transaction() as tx:  # pairs move together, atomically
                tx.put("rules", {"id": "r1", "gen": gen})
                tx.put("rules", {"id": "r2", "gen": gen})

    t = threading.Thread(target=writer)
    t.start()
    try:
        b = make_backup(s3, store, clock)
        for _ in range(5):
            b.snapshot()
            clock.advance(hours=24)
    finally:
        stop.set()
        t.join()
    for rec in b.list_backups():
        target = MemoryStore()
        b.restore(target, upto=rec.created_at)
        assert target.get("rules", "r1")["gen"] == target.get("rules", "r2")["gen"]


# --------------------------------------------------------------- restore


def test_restore_reproduces_everything(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    b.snapshot()
    target = MemoryStore()
    report = b.restore(target)
    assert dump(target) == dump(store)
    assert report.documents == 7
    assert report.rto_seconds >= 0


def test_restore_refuses_non_empty_target(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    b.snapshot()
    target = MemoryStore()
    target.put("rules", {"id": "stray"})
    with pytest.raises(BackupError, match="not empty"):
        b.restore(target)


def test_restore_with_nothing_to_restore(s3, clock):
    with pytest.raises(BackupError, match="no snapshot"):
        make_backup(s3, MemoryStore(), clock).restore(MemoryStore())


def test_increments_carry_run_history_changes(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    b.snapshot()
    clock.advance(hours=1)
    store.put("runs", {"id": "run3", "state": "new"})
    store.put("runs", {"id": "run1", "state": "archived"})
    store.delete("runs", "run2")
    store.put("rules", {"id": "r-late"})  # config is NOT in increments
    inc = b.increment()
    assert inc.kind == "increment" and inc.counts["runs"] == 3
    target = MemoryStore()
    b.restore(target)
    assert target.get("runs", "run3")["state"] == "new"
    assert target.get("runs", "run1")["state"] == "archived"
    assert target.get("runs", "run2") is None
    assert target.get("rules", "r-late") is None


def test_increments_chain_without_gaps_or_duplicates(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    b.snapshot()
    for n in range(3):
        clock.advance(hours=1)
        store.put("runs", {"id": f"x{n}"})
        rec = b.increment()
        assert rec.counts["runs"] == 1
    clock.advance(hours=1)
    assert b.increment().counts["runs"] == 0  # nothing new
    target = MemoryStore()
    b.restore(target)
    assert dump(target, ("runs",)) == dump(store, ("runs",))


def test_increment_without_snapshot_fails(s3, clock):
    with pytest.raises(BackupError, match="snapshot"):
        make_backup(s3, MemoryStore(), clock).increment()


def test_restore_upto_point_in_time(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    b.snapshot()
    clock.advance(hours=1)
    store.put("runs", {"id": "early"})
    first = b.increment()
    clock.advance(hours=1)
    store.put("runs", {"id": "late"})
    b.increment()
    target = MemoryStore()
    b.restore(target, upto=first.created_at)
    assert target.get("runs", "early") and target.get("runs", "late") is None


def test_restore_detects_corruption(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    rec = b.snapshot()
    s3.put_object(Bucket=BUCKET, Key=rec.key, Body=b"not gzip", ServerSideEncryption="AES256")
    with pytest.raises(BackupError):
        b.restore(MemoryStore())


# --------------------------------------------------------------- schedule


def test_schedule_daily_snapshot_hourly_increment(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    assert b.due(clock()) == "snapshot"
    assert b.tick().kind == "snapshot"
    assert b.due(clock()) is None and b.tick() is None
    clock.advance(minutes=59)
    assert b.tick() is None
    clock.advance(minutes=1)
    assert b.due(clock()) == "increment"
    assert b.tick().kind == "increment"
    for _ in range(22):
        clock.advance(hours=1)
        assert b.tick().kind == "increment"
    clock.advance(hours=1)  # 24 h after the snapshot
    assert b.tick().kind == "snapshot"
    clock.advance(hours=1)
    assert b.tick().kind == "increment"


# ------------------------------------------------------------ drill + cli


def test_drill_reports_rpo_and_rto(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    b.snapshot()
    clock.advance(minutes=40)
    store.put("runs", {"id": "r9"})
    b.increment()
    clock.advance(minutes=20)
    target = MemoryStore()
    result = b.drill(target, source=store)
    assert result.verified is True
    assert result.rpo_seconds == pytest.approx(20 * 60)
    assert result.rto_seconds >= 0
    assert result.mismatches == []


def test_drill_flags_mismatch(s3, clock):
    store = MemoryStore()
    seed(store)
    b = make_backup(s3, store, clock)
    b.snapshot()
    store.put("rules", {"id": "unbacked"})
    result = b.drill(MemoryStore(), source=store)
    assert result.verified is False and result.mismatches


def test_cli_main_snapshot_and_restore(s3, clock, monkeypatch, capsys):
    from culture_rules.ops import backup as mod

    store = MemoryStore()
    seed(store)
    target = MemoryStore()
    stores = iter([store, target])
    monkeypatch.setenv("CULTURE_RULES_BACKUP_BUCKET", BUCKET)
    monkeypatch.setenv("CULTURE_RULES_BACKUP_REGION", REGION)
    monkeypatch.setattr(mod, "open_store", lambda: next(stores))
    assert mod.main(["snapshot", "--json"]) == 0
    assert '"kind": "snapshot"' in capsys.readouterr().out
    assert mod.main(["restore", "--json"]) == 0
    assert dump(target) == dump(store)
    assert mod.main(["bogus"]) == 1
