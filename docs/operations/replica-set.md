# Operations: the MongoDB replica set

culture-rules persists runs, rules and workflows in a MongoDB replica set that
spans the hosts. This page is the deployment recipe, the topology decision and
the operator checklist. Artifacts live in `deploy/replica-set/`.

## Topology and the third-voter decision

| Member | Host | Votes | Priority | Role |
|--------|------|-------|----------|------|
| spark | spark | 1 | 2 | preferred primary |
| thor | thor | 1 | 1 | voting data member |
| orin | orin | 1 | 1 | **always-up third voter** (data-bearing) |
| spark2 | spark2 | 0 | 0 | non-voting data member, may be offline |

**Decision: the third voter is orin, a full data-bearing member, not an
arbiter.** spark2 is routinely offline, so it must not count toward quorum.
That leaves three voters: spark, thor and orin. A majority is 2 of 3, so any
one voter can be down and a primary still exists.

Why not an arbiter on orin: with voters spark, thor and an arbiter, losing
either data member leaves a single data-bearing node. `w: majority` needs 2
acknowledgements, and an arbiter cannot acknowledge a write, so majority writes
stall (and majority reads on a one-data-node set lag). A data-bearing orin
keeps two real copies whenever one voter is down. An arbiter is only the
fallback if orin cannot host a data directory.

spark2 stays in the set as a non-voting, priority-0 member: it holds a copy
for local reads and backup, and its absence never affects elections or
majority commit. Trade-off: spark2 can never become primary, so the database
primary is always spark, thor or orin; the application (editor, API, runners)
still runs on spark2 and reaches the primary over the tailnet.

Guarantee: with spark2 offline **and** any one of spark, thor or orin stopped,
the remaining two voters elect a primary and accept `w: majority` writes.
Losing two voters (for example spark and thor) loses the majority, by design.

## Port, authentication and TLS

- Each host runs its **own dedicated mongod** on port **27018** (set by
  `MONGO_PORT`). It never uses the default 27017, so it cannot collide with any
  other MongoDB on the host.
- Members authenticate to each other with a shared **keyFile**
  (`--keyFile`). Clients authenticate with SCRAM users (see below).
- All traffic is **TLS** (`--tlsMode requireTLS`) under a private CA.
  Clients present no certificate by default
  (`--tlsAllowConnectionsWithoutCertificates`); they verify the server against
  `ca.pem`, which maps to `CULTURE_RULES_MONGO_TLS_CA_FILE`.
- **x509 alternative:** to replace the keyFile with x509 membership auth, issue
  each `server.pem` from the same CA with an `O`/`OU` shared by all members,
  and swap `--keyFile` for `--clusterAuthMode x509`. The shipped default is
  keyFile + TLS because it needs no extra certificate attributes.

## Files

| File | Purpose |
|------|---------|
| `deploy/replica-set/compose.yaml` | one mongod per host via docker compose |
| `deploy/replica-set/mongod.service` | systemd alternative to compose |
| `deploy/replica-set/replica-set.env.example` | per-host settings (port, paths) |
| `deploy/replica-set/gen-certs.sh` | generates keyFile, CA and per-host `server.pem` |
| `deploy/replica-set/rs-config.json` | `rs.initiate` config with `@host@` placeholders |

Never commit generated secrets. Keep them under `/etc/culture-rules-mongo`
(root-only) on each host.

## Operator checklist

These are hand-turns on real hosts. The build does not perform them.

1. Pick a trusted machine and generate secrets once:
   `deploy/replica-set/gen-certs.sh ./secrets spark thor orin spark2`
   (use the names or IPs members are reached by, for example tailnet names).
2. On each host, create `/etc/culture-rules-mongo` and copy `keyfile`,
   `ca.pem` and that host's `server.pem` into it. Mode 400 for the keyfile.
3. Copy `replica-set.env.example` to `replica-set.env`; confirm
   `MONGO_PORT=27018` and that nothing else listens on it.
4. Start mongod on all four hosts: `docker compose --env-file replica-set.env up -d`
   (or install `mongod.service` and `systemctl enable --now`).
5. Open port 27018 between the four hosts on the tailnet only; do not expose
   it publicly.
6. Make a copy of `rs-config.json`, replace `@spark@`, `@thor@`, `@orin@` and
   `@spark2@` with `host:27018` addresses, and run `rs.initiate(<config>)`
   from `mongosh --tls --tlsCAFile ca.pem --port 27018` on spark (localhost
   exception, before any user exists).
7. Create the root user, then an application user with `readWrite` on the
   culture-rules database only, using `writeConcern: {w: "majority"}`.
8. Set the application environment: `CULTURE_RULES_MONGO_URI`
   (all four seeds, `replicaSet=culture-rules-rs`),
   `CULTURE_RULES_MONGO_DB` and `CULTURE_RULES_MONGO_TLS_CA_FILE`.
9. Confirm `rs.status()` shows three voters plus spark2 with `votes: 0`.
10. Run the chaos drill below on the real set once, outside business hours.

## Chaos check

Recorded in `tests/store/test_replica_set_deploy.py` (marker `mongo`, needs
docker, openssl and the `mongo:8.0` image). It builds the real
`rs-config.json` into four local containers on random ports, with keyFile and
TLS, then for each of spark, thor and orin: stops spark2 and that member,
asserts a primary still acknowledges an authenticated `w: majority` insert,
reads it back with majority read concern, and restarts both. A negative
control stops spark and thor and asserts that majority writes fail.

Run it with `uv run pytest tests/store/test_replica_set_deploy.py -m mongo -v`.
The drill on the real hosts (checklist step 10) is the same procedure with
`docker stop` on spark2 and one other host.
