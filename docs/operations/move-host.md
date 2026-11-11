# Moving culture-rules to another host

This runbook moves the control plane (the store, the API and its Access tunnel) and
the engine node from one machine to another, with the rules, workflows and actors
following them. It names no workflow, actor or host: those are data in the store,
and the move only changes where they run. `cr` below is
`culture-rules --api-url <the API's LAN listener>` run with a service token, and
every write is a dry run until `--apply`.

## Why the node must sit next to the store

The node makes many store round trips, one after another, on each tick: it
synthesises schedule slots, claims events, starts runs and records every step. On
the same host each round trip costs well under a millisecond. Across a link of tens
of milliseconds, a tick takes minutes, schedules fire late and probes miss their
slot. So move the node in the same session as the store, and never leave the only
engine node behind on a slow link once the store has moved.

## What decides where work runs

Read this before moving anything. It is what decides which steps follow you.

- **A rule runs where its `placement` says** (a machine, an actor or a requirement).
  A schedule or probe trigger is synthesised only by the node its placement resolves
  to.
- **An action step that names an actor** in `params.actor` runs on that actor's
  `machine` (its credentials are injected there), unless the step has a placement of
  its own. Moving an actor's `machine` moves all of those steps.
- **A built-in code step with no placement** goes to the first eligible machine by
  name. A machine is eligible when it is enrolled, enabled, online and not drained,
  with a LAN or tailnet address. So **enrolling a machine whose name sorts first moves
  every unplaced built-in step to it the moment its node comes online.** That is safe
  for steps that keep their state in the store or only transform data. It is not safe
  for a step that reads or writes files on a node's disk: give that step a
  `placement.machine`, and move its files with it.
- **A step that reads files** names a root (`content_root`, `root`, `output_root`,
  `save_to`, an actor's `content_roots`, `audio_root`, ...). Those are absolute paths
  on one machine. If the new host has a different home directory, every such path
  changes with the move.

## Before you start

1. **Inventory with `--json` dumps** of `rules list`, `workflows show <id>` for each
   workflow, and `actors list`. Find every rule and actor placed on the old host,
   every step with `placement.machine` set to it, and every path under its home
   directory.
2. **List the services those actors call on the old host's loopback** (an actor
   `base_url` or `http.allow` of `127.0.0.1`). Each one either moves with the engine
   or becomes a LAN address.
3. **List what the old host feeds into the event bus.** A publisher that stays
   behind still has to reach the broker after it moves.
4. **Check the secrets.** Every grant reference the node unit injects
   (`CULTURE_RULES_SECRET_<NAME>`) and every one the actors name must exist in grant
   on the new host. `grant show NAME` checks without printing a value; to compare a
   value across hosts, compare a hash of it inside `grant run --inject`.
5. **Check the model names.** An agent or STT actor names a model on a lobes
   gateway. The new host's gateway may serve it under an alias only, so try the exact
   `model` value against the new gateway before repointing.

## The order

1. **Store and API.** Add the new host to the replica set, let it catch up, promote
   it and remove the old member (see [replica-set.md](replica-set.md)), so the oplog
   and change-stream cursors carry over. Then move the API unit and its Access
   tunnel, and point every node and the API at the new primary. Both read
   `CULTURE_RULES_MONGO_URI`; a node unit injects it from a grant secret
   (`RULES_MONGO_URI` by default, see
   [deploy/node/README.md](../../deploy/node/README.md)), so update that secret.
2. **Services the engine calls.** Move or expose each loopback service from the
   inventory. While anything on the old host still needs a moved service, a **reverse
   ssh tunnel run from the new host** keeps the old loopback address working without
   giving the old host any access to the new one:

   ```bash
   ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
     -R 127.0.0.1:<port>:127.0.0.1:<port> <old-host>
   ```

   Run it as a user unit with `Restart=always`, and remove it as soon as the last
   user on the old host has moved.
3. **Pin node-local file steps.** Any unplaced built-in step that reads node-local
   files and must stay behind for now gets a `placement.machine` before the next
   step.
4. **Enrol the new machine** with `cr machines create --body '{"name": ...,
   "address": ..., "roles": ["engine_node", "runner"], ...}' --apply`. The body
   takes no `id`; the name is the id. An offline machine is never eligible, so
   enrolling before the node exists changes nothing yet.
5. **Copy node-local state** the moved steps expect: file roots, cursor and state
   files, caches. Copy it now, before the node starts: an unplaced built-in step can
   run on the new host as soon as it is online.
6. **Start the node** as a user unit: `culture-rules node run --host <name>` under
   `grant run`, injecting the same app secrets as the old node, minus any that no
   longer have a consumer. From here, unplaced built-in steps run on the new host
   if its name sorts first.
7. **Move the actors.** `cr actors update <id> --body @actor.json --apply` replaces
   the whole actor, and the body must carry its `id`. Change `machine`, every
   absolute path, and any `base_url` or `model` the new host reaches differently.
   For a runner whose command `argv` names paths on the old host, create a new runner
   on the new host and repoint to it rather than editing the old one in place.
   Listener-owning app actors (a Discord gateway) hand their lease over by
   themselves: the old node stops listening and the new one takes the lease.
8. **Move the workflows.** `cr workflows update <id> --body @wf.json
   --expected-revision <rev> --apply`: change each `placement.machine` and each
   absolute path. `workflows repoint-actor` moves a workflow from one actor to another
   when that is the only change.
9. **Move the rules.** `cr rules update <id> --body @rule.json --apply`: change
   `placement.machine`, and a probe trigger's `params.actor` if its runner changed.

## Verify, then retire the old node

- **Wait for one scheduled run** of a frequent rule and check it ran on the new host
  (`cr runs list` shows `hosts`) close to its slot. Then watch each other kind of
  work run once: an event-triggered rule, an actor-bound action, a file step.
- **Then stop and disable the old node**, and set `CULTURE_RULES_NODE_NAME` in the
  API's environment to the new node so `/health` reports it (an API restart).
- **Remove the transition tunnels**, and disable or delete actors that only made
  sense on the old host. Leave the old host enrolled but offline, or disable it; an
  offline machine never receives work.

## Rollback

Until the old host's copies are deleted, rolling back is the same moves in reverse:
start the old node, set the actors, workflows and rules back to the old machine and
paths, and stop the new node. Keep the old host's stopped services and data, and its
unit files, until the new host has run every kind of work at least once. Back a unit
file up before deleting it, and gate the delete on the backup having succeeded.
