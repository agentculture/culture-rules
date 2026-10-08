# Backup and restore drill

culture-rules backs up config (`rules`, `workflows`, `actors`, `machines`) and
run history (`runs`, `audit`, `run_completions`) to an S3 bucket with
server-side encryption. A backup chain written before `run_completions` was
added has no token for it, so the schedule takes a new snapshot first.
The library is `culture_rules/ops/backup.py`; its module docstring is the
contract.

## Schedule

- A snapshot of everything every 24 hours.
- A run-history increment every hour (config changes wait for the next
  snapshot).

Run one entry point hourly. `tick` decides what is due, so a missed run
catches up:

```cron
5 * * * * cd /srv/culture-rules && uv run --extra backup --extra store python -m culture_rules.ops.backup tick --json
```

## Configuration

Nothing here is committed. Set it in the environment of the cron/systemd unit,
or store values with grant (`grant set NAME`).

| Variable | Meaning |
|----------|---------|
| `CULTURE_RULES_BACKUP_BUCKET` | Bucket name (required). Versioning must be enabled. |
| `CULTURE_RULES_BACKUP_REGION` | Region (required). |
| `CULTURE_RULES_BACKUP_PREFIX` | Key prefix, default `culture-rules`. |
| `CULTURE_RULES_BACKUP_SSE` | `AES256` (default) or `aws:kms`. |
| `CULTURE_RULES_BACKUP_KMS_KEY_ID` | KMS key for `aws:kms`. |
| `CULTURE_RULES_BACKUP_ENDPOINT_URL` | S3-compatible endpoint (MinIO). |
| `CULTURE_RULES_BACKUP_ACCESS_KEY_ID` / `..._SECRET_ACCESS_KEY` | Optional; a `grant:<NAME>` reference is resolved at run time with `grant get NAME`. Default is the AWS credential chain (profile, role). |
| `CULTURE_RULES_MONGO_URI` and friends | The store being backed up or restored into. |

One-time bucket setup (operator): enable versioning
(`aws s3api put-bucket-versioning --bucket "$CULTURE_RULES_BACKUP_BUCKET" --versioning-configuration Status=Enabled`).
The tool refuses to write to an unversioned bucket and verifies every object
reports server-side encryption.

## Restore drill (operator, real S3)

Targets: RPO <= 1 hour, RTO <= 30 minutes. Run against a **scratch** replica
set, never production.

```bash
export CULTURE_RULES_BACKUP_BUCKET=... CULTURE_RULES_BACKUP_REGION=...
# 1. take a fresh snapshot + increment from production
uv run python -m culture_rules.ops.backup snapshot --json
uv run python -m culture_rules.ops.backup increment --json
# 2. point the Mongo env at an EMPTY scratch replica set, then drill
export CULTURE_RULES_MONGO_URI=... CULTURE_RULES_MONGO_DB=restore_drill
uv run python -m culture_rules.ops.backup drill --json
```

`drill` reports `rpo_seconds` (age of the newest backup object when the drill
started: the worst-case data loss) and `rto_seconds` (wall time of the
restore). To verify content against production, compare document counts per
collection, or run the library `Backup.drill(target, source=prod_store)`,
which lists every mismatching document.

### Results

Fill one row per drill.

| Date | Operator | Bucket region | SSE | Documents | RPO (s) | RTO (s) | Verified | Notes |
|------|----------|---------------|-----|-----------|---------|---------|----------|-------|
|      |          |               |     |           |         |         |          |       |

Pass: RPO <= 3600 and RTO <= 1800 and no mismatches. Until a row is filled in,
the real-S3 RPO/RTO acceptance is not met; the moto and Mongo-rig tests only
prove the mechanism.
