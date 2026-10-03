"""Encrypted, versioned S3 backups of config and run history, plus a restore drill.

What is backed up
=================

*Config* collections (``rules``, ``workflows``, ``actors``, ``machines``) and
*run history* collections (``runs``, ``audit``) read through the
:class:`~culture_rules.store.port.StoragePort`. Both lists are configurable.

Objects (all gzip-compressed JSON, written with server-side encryption)::

    <prefix>/snapshots/<ts>.json.gz    full copy of every collection (daily)
    <prefix>/increments/<ts>.json.gz   run-history changes since the previous object (hourly)

A snapshot records, per run-history collection, the change-feed token it is
consistent with; an increment records the changes after its base object's
tokens and its own new tokens, naming its ``base`` key so restore can verify
the chain has no gap. The bucket must have **versioning enabled** (checked
before every write), so an overwrite or delete is always recoverable.

Consistency
===========

The port has no read snapshot, so a snapshot takes each collection's feed
``head`` first, scans, and then replays the change feed from that head over the
scanned documents (post-images are authoritative). The result is each
collection exactly as of the last replayed change, with no torn or stale
document even while writers run. Different collections are consistent
document-by-document, not as one cross-collection instant; transactions that
span collections can therefore be seen half-applied across collections in a
snapshot, and the hourly increments repair run history forward.

Configuration (config/env/shushu only; nothing is committed)
===========================================================

``CULTURE_RULES_BACKUP_BUCKET``, ``CULTURE_RULES_BACKUP_REGION`` (required),
``CULTURE_RULES_BACKUP_PREFIX`` (default ``culture-rules``),
``CULTURE_RULES_BACKUP_SSE`` (``AES256`` default, or ``aws:kms``),
``CULTURE_RULES_BACKUP_KMS_KEY_ID``, ``CULTURE_RULES_BACKUP_ENDPOINT_URL``
(S3-compatible stores such as MinIO). Credentials come from the standard AWS
chain (env, profile, role); the optional ``CULTURE_RULES_BACKUP_ACCESS_KEY_ID``
and ``CULTURE_RULES_BACKUP_SECRET_ACCESS_KEY`` may be a ``shushu:<name>``
reference, resolved at run time by ``shushu get <name>``.

CLI: ``python -m culture_rules.ops.backup {snapshot,increment,tick,restore,list,drill}``.
``tick`` is the scheduler entry point: run it hourly from cron/systemd; it takes
a snapshot when the newest one is >= 24 h old and an increment when the newest
object is >= 1 h old.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import subprocess  # nosec B404 - only used to call the operator's shushu CLI
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from culture_rules.store.port import StoragePort

__all__ = [
    "Backup",
    "BackupConfig",
    "BackupConfigError",
    "BackupError",
    "BackupRecord",
    "DrillResult",
    "RestoreReport",
    "main",
    "open_store",
    "resolve_secret",
]

CONFIG_COLLECTIONS = ("rules", "workflows", "actors", "machines")
RUN_COLLECTIONS = ("runs", "audit")
SNAPSHOT_INTERVAL = timedelta(hours=24)
INCREMENT_INTERVAL = timedelta(hours=1)
_SSE_MODES = ("AES256", "aws:kms")
_FORMAT = 1


class BackupError(Exception):
    """A backup, restore or drill failed."""


class BackupConfigError(BackupError):
    """Backup configuration is missing or invalid."""


def resolve_secret(value: str, runner: Callable[[str], str] | None = None) -> str:
    """Return ``value``, or resolve a ``shushu:<name>`` reference at run time."""
    if not value.startswith("shushu:"):
        return value
    name = value[len("shushu:") :]
    return (runner or _shushu_get)(name)


def _shushu_get(name: str) -> str:
    try:
        done = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["shushu", "get", name], capture_output=True, text=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupConfigError(f"cannot resolve secret reference shushu:{name}: {exc}") from exc
    return done.stdout.strip()


@dataclass(frozen=True)
class BackupConfig:
    bucket: str
    region: str
    prefix: str = "culture-rules"
    sse: str = "AES256"
    kms_key_id: str | None = None
    endpoint_url: str | None = None
    access_key_id: str | None = field(default=None, repr=False)
    secret_access_key: str | None = field(default=None, repr=False)
    config_collections: tuple[str, ...] = CONFIG_COLLECTIONS
    run_collections: tuple[str, ...] = RUN_COLLECTIONS

    def __post_init__(self) -> None:
        if not self.bucket or not self.region:
            raise BackupConfigError("bucket and region are required")
        if self.sse not in _SSE_MODES:
            raise BackupConfigError(f"sse must be one of {_SSE_MODES}; backups are never plain")
        if self.sse != "aws:kms" and self.kms_key_id:
            raise BackupConfigError("kms_key_id requires sse='aws:kms'")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> BackupConfig:
        env = os.environ if environ is None else environ
        bucket = env.get("CULTURE_RULES_BACKUP_BUCKET", "")
        region = env.get("CULTURE_RULES_BACKUP_REGION", "")
        if not bucket:
            raise BackupConfigError("CULTURE_RULES_BACKUP_BUCKET is not set")
        if not region:
            raise BackupConfigError("CULTURE_RULES_BACKUP_REGION is not set")

        def secret(name: str) -> str | None:
            raw = env.get(name)
            return resolve_secret(raw) if raw else None

        return cls(
            bucket=bucket,
            region=region,
            prefix=env.get("CULTURE_RULES_BACKUP_PREFIX", "culture-rules").strip("/"),
            sse=env.get("CULTURE_RULES_BACKUP_SSE", "AES256"),
            kms_key_id=env.get("CULTURE_RULES_BACKUP_KMS_KEY_ID") or None,
            endpoint_url=env.get("CULTURE_RULES_BACKUP_ENDPOINT_URL") or None,
            access_key_id=secret("CULTURE_RULES_BACKUP_ACCESS_KEY_ID"),
            secret_access_key=secret("CULTURE_RULES_BACKUP_SECRET_ACCESS_KEY"),
        )


@dataclass(frozen=True)
class BackupRecord:
    key: str
    kind: str  # "snapshot" | "increment"
    created_at: datetime
    counts: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RestoreReport:
    snapshot_key: str
    increments: int
    documents: int
    rto_seconds: float
    restored_to: datetime


@dataclass(frozen=True)
class DrillResult:
    verified: bool
    mismatches: list[str]
    rpo_seconds: float
    rto_seconds: float
    documents: int
    restored_to: datetime


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def _parse_stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=UTC)


def _strip(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if k != "updated_at"}


class Backup:
    """Take, schedule, restore and drill backups of one StoragePort into one bucket."""

    def __init__(
        self,
        config: BackupConfig,
        store: StoragePort,
        *,
        client: Any = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))
        self._checked = False

    # ------------------------------------------------------------- S3 plumbing

    @property
    def client(self) -> Any:
        if self._client is None:
            try:
                import boto3  # noqa: PLC0415 - optional 'backup' extra, imported lazily
            except ImportError as exc:
                raise BackupError("boto3 is required: pip install 'culture-rules[backup]'") from exc
            kwargs: dict[str, Any] = {"region_name": self.config.region}
            if self.config.endpoint_url:
                kwargs["endpoint_url"] = self.config.endpoint_url
            if self.config.access_key_id and self.config.secret_access_key:
                kwargs["aws_access_key_id"] = self.config.access_key_id
                kwargs["aws_secret_access_key"] = self.config.secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _require_versioning(self) -> None:
        if self._checked:
            return
        status = self.client.get_bucket_versioning(Bucket=self.config.bucket).get("Status")
        if status != "Enabled":
            raise BackupError(
                f"bucket {self.config.bucket!r} must have versioning enabled (status: {status})"
            )
        self._checked = True

    def _put(self, kind: str, created: datetime, body: dict[str, Any], counts: dict[str, int]):
        self._require_versioning()
        key = f"{self.config.prefix}/{kind}s/{_stamp(created)}.json.gz"
        args: dict[str, Any] = {
            "Bucket": self.config.bucket,
            "Key": key,
            "Body": gzip.compress(json.dumps(body, sort_keys=True).encode()),
            "ServerSideEncryption": self.config.sse,
            "Metadata": {"counts": json.dumps(counts, sort_keys=True)},
        }
        if self.config.kms_key_id:
            args["SSEKMSKeyId"] = self.config.kms_key_id
        self.client.put_object(**args)
        stored = self.client.head_object(Bucket=self.config.bucket, Key=key)
        if stored.get("ServerSideEncryption") != self.config.sse:
            raise BackupError(f"{key} was stored without server-side encryption")
        return BackupRecord(key, kind, created, counts)

    def _load(self, key: str) -> dict[str, Any]:
        try:
            raw = self.client.get_object(Bucket=self.config.bucket, Key=key)["Body"].read()
            body = json.loads(gzip.decompress(raw))
        except (OSError, EOFError, ValueError) as exc:
            raise BackupError(f"backup object {key} is corrupt or unreadable: {exc}") from exc
        if not isinstance(body, dict) or body.get("format") != _FORMAT:
            raise BackupError(f"backup object {key} has an unknown format")
        return body

    def list_backups(self) -> list[BackupRecord]:
        """Every snapshot and increment, oldest first."""
        records: list[BackupRecord] = []
        token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self.config.bucket,
                "Prefix": f"{self.config.prefix}/",
            }
            if token:
                kwargs["ContinuationToken"] = token
            page = self.client.list_objects_v2(**kwargs)
            for obj in page.get("Contents", []):
                key = obj["Key"]
                parts = key[len(self.config.prefix) + 1 :].split("/")
                if len(parts) != 2 or parts[0] not in ("snapshots", "increments"):
                    continue
                created = _parse_stamp(parts[1].removesuffix(".json.gz"))
                kind = parts[0][:-1]
                records.append(BackupRecord(key, kind, created, {}))
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
        return sorted(records, key=lambda r: (r.created_at, r.key))

    # ---------------------------------------------------------------- snapshot

    def snapshot(self) -> BackupRecord:
        """Write a consistent full snapshot of config and run history."""
        collections = (*self.config.config_collections, *self.config.run_collections)
        before = {c: self.store.head(c) for c in collections}
        scanned = {c: {d["id"]: d for d in self.store.find(c)} for c in collections}
        tokens = dict(before)
        for c in collections:
            after = self.store.head(c)
            if after == before[c]:
                continue
            for change in self.store.changes(c, before[c]):
                if change.document is None:
                    scanned[c].pop(change.id, None)
                else:
                    scanned[c][change.id] = change.document
                tokens[c] = change.token
                if change.token == after:
                    break
        created = self._clock()
        counts = {c: len(docs) for c, docs in scanned.items()}
        body = {
            "format": _FORMAT,
            "kind": "snapshot",
            "created_at": created.isoformat(),
            "tokens": {c: tokens[c] for c in self.config.run_collections},
            "collections": {
                c: sorted(d.values(), key=lambda x: x["id"]) for c, d in scanned.items()
            },
        }
        return self._put("snapshot", created, body, counts)

    def increment(self) -> BackupRecord:
        """Write the run-history changes since the newest backup object."""
        records = self.list_backups()
        if not records:
            raise BackupError("no snapshot exists yet; take a snapshot before an increment")
        base = records[-1]
        tokens = dict(self._load(base.key)["tokens"])
        changes: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for c in self.config.run_collections:
            n = 0
            for change in self.store.changes(c, tokens[c]):
                changes.append(
                    {
                        "collection": c,
                        "op": change.op,
                        "id": change.id,
                        "document": change.document,
                    }
                )
                tokens[c] = change.token
                n += 1
            counts[c] = n
        created = self._clock()
        body = {
            "format": _FORMAT,
            "kind": "increment",
            "created_at": created.isoformat(),
            "base": base.key,
            "tokens": tokens,
            "changes": changes,
        }
        return self._put("increment", created, body, counts)

    # ---------------------------------------------------------------- schedule

    def due(self, now: datetime) -> str | None:
        """``"snapshot"`` / ``"increment"`` / None: what the schedule wants at ``now``."""
        records = self.list_backups()
        snaps = [r for r in records if r.kind == "snapshot"]
        if not snaps or now - snaps[-1].created_at >= SNAPSHOT_INTERVAL:
            return "snapshot"
        if now - records[-1].created_at >= INCREMENT_INTERVAL:
            return "increment"
        return None

    def tick(self) -> BackupRecord | None:
        """Run whatever the schedule says is due (daily snapshot, hourly increment)."""
        what = self.due(self._clock())
        if what == "snapshot":
            return self.snapshot()
        if what == "increment":
            return self.increment()
        return None

    # ----------------------------------------------------------------- restore

    def restore(self, target: StoragePort, *, upto: datetime | None = None) -> RestoreReport:
        """Restore the newest snapshot (+ its increments) at or before ``upto`` into ``target``."""
        started = time.monotonic()
        records = [r for r in self.list_backups() if upto is None or r.created_at <= upto]
        snaps = [r for r in records if r.kind == "snapshot"]
        if not snaps:
            raise BackupError("no snapshot to restore from")
        collections = (*self.config.config_collections, *self.config.run_collections)
        ensure = getattr(target, "ensure_collections", None)
        if ensure is not None:
            ensure(*collections)
        for c in collections:
            if target.find(c, limit=1):
                raise BackupError(f"target is not empty: collection {c!r} has documents")
        snap = snaps[-1]
        body = self._load(snap.key)
        documents = 0
        for c, docs in body["collections"].items():
            for doc in docs:
                target.put(c, doc)
                documents += 1
        chain, restored_to, tip = 0, snap.created_at, snap
        for rec in (r for r in records if r.kind == "increment" and r.created_at > snap.created_at):
            inc = self._load(rec.key)
            if inc["base"] != tip.key:
                continue  # belongs to a different (older or newer) chain
            for change in inc["changes"]:
                if change["op"] == "delete":
                    target.delete(change["collection"], change["id"])
                else:
                    target.put(change["collection"], change["document"])
            chain, restored_to, tip = chain + 1, rec.created_at, rec
        return RestoreReport(snap.key, chain, documents, time.monotonic() - started, restored_to)

    def drill(self, target: StoragePort, *, source: StoragePort | None = None) -> DrillResult:
        """Restore into ``target``, measure RPO/RTO, and verify against ``source`` if given."""
        now = self._clock()
        records = self.list_backups()
        if not records:
            raise BackupError("no backups to drill against")
        rpo = (now - records[-1].created_at).total_seconds()
        report = self.restore(target)
        mismatches: list[str] = []
        if source is not None:
            for c in (*self.config.config_collections, *self.config.run_collections):
                want = {d["id"]: _strip(d) for d in source.find(c)}
                got = {d["id"]: _strip(d) for d in target.find(c)}
                for doc_id in sorted(want.keys() | got.keys()):
                    if want.get(doc_id) != got.get(doc_id):
                        mismatches.append(f"{c}/{doc_id}")
        return DrillResult(
            not mismatches,
            mismatches,
            rpo,
            report.rto_seconds,
            report.documents,
            report.restored_to,
        )


# --------------------------------------------------------------------------- CLI


def open_store() -> StoragePort:
    """Open the production store from env (MongoDB replica set)."""
    from culture_rules.store.mongo import MongoConfig, MongoStore  # noqa: PLC0415

    store = MongoStore(MongoConfig.from_env())
    store.ensure_collections(*CONFIG_COLLECTIONS, *RUN_COLLECTIONS)
    return store


def _jsonable(obj: Any) -> Any:
    data = asdict(obj) if obj is not None else None
    if data is None:
        return None
    return {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in data.items()}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="culture_rules.ops.backup", description=__doc__)
    parser.add_argument(
        "command", choices=["snapshot", "increment", "tick", "restore", "list", "drill"]
    )
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1
    try:
        store = open_store()
        backup = Backup(BackupConfig.from_env(), store)
        if args.command == "list":
            result: Any = [_jsonable(r) for r in backup.list_backups()]
        elif args.command == "restore":
            result = _jsonable(backup.restore(store))  # the opened store is the target
        elif args.command == "drill":
            result = _jsonable(backup.drill(store))  # restore into an empty replica set
        else:
            result = _jsonable(getattr(backup, args.command)())
    except BackupConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - structured failure, no traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True) if args.json else result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
