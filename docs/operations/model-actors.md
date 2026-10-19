# Model actors

An endpoint actor is an `agent` actor with `params.base_url`: it talks to an
OpenAI-compatible gateway, and `llm.complete` steps run on it. This page covers
the verbs that enrol a gateway's models as endpoint actors, show what uses an
actor, and move a workflow from one actor to another. They need culture-rules
0.22.0 or later on the API; an older server makes each verb exit 2 with
"server too old: needs culture-rules >= 0.22.0".

## The `model_gateways` variable

`enrol-models` only calls gateways an admin listed in the shared variable
`model_gateways`. It holds a **list** of `"machine=URL"` strings; an object, a
repeated machine, a non-http(s) URL or an item without `=` is refused with 409
`invalid_model_gateways`:

```bash
culture-rules variables set model_gateways \
  --value '["spark=http://127.0.0.1:8001", "spark2=http://<spark2-host>:8000"]' --apply
```

`--gateway` takes a machine name from this list. A name that is not in it is
refused (422 `unknown_gateway`, naming the known ones). The check runs first:
the API resolves the `--api-key` grant only for an allow-listed gateway, and
sends the bearer to that URL only (`<URL>/v1/models`, or `<URL>/models` when the
URL already ends in `/v1`). The variable is read on every call, so a change
applies at once.

## The verbs

| Verb | Role | What it does |
|---|---|---|
| `actors enrol-models` | admin | plans, then writes, one endpoint actor per matched model |
| `actors references ID` | viewer | lists the workflows and rules that name an actor |
| `actors disable ID [--force]` | editor | disables an actor; refused while it is in use |
| `workflows repoint-actor WF --from A --to B` | editor | points a workflow's steps at another actor |

Writes are dry-run unless `--apply`; every verb takes `--json`; each has an MCP
tool of the same name (`actors_enrol_models`, `actors_references`,
`actors_disable`, `workflows_repoint_actor`).

**`actors enrol-models --gateway MACHINE (repeatable) --match GLOB (repeatable)
[--pool MODEL=POOL] [--api-key grant:NAME] [--apply]`**

- `--gateway` repeats; the order is the priority. A model served by several
  gateways gets its actor from the first one; the others show as
  `also_served_by`. `--match` repeats and is required (no default glob).
- Each matched model becomes the actor with `id` = the model id, `kind: agent`,
  `machine` = the gateway's machine, and `params`: `base_url` (the allow-listed
  URL), `model`, `api_key`, `max_concurrency` and `concurrency_pool` (default:
  the model id; `--pool MODEL=POOL` overrides it). `--api-key` is a `grant:<NAME>` reference
  (default `grant:LOBES_GATEWAY_API_KEY`); a literal key is refused (422
  `invalid_api_key`) and never echoed.
- A model id must be one URL segment (letters, digits, `.`, `_`, `-`), so a name
  such as `unsloth/Qwen...` is refused.
- Re-running is idempotent: a model whose actor already matches is `unchanged`;
  an update touches only the enrolled fields (`machine`, the endpoint `params`;
  an existing `max_concurrency` is kept), not a hand-edited description. An id
  held by a non-agent actor, an agent without `base_url` or a deleted actor is
  planned as `refused`. Actors have no revisions, so the result lists
  ids and actions, not revision numbers.
- The result also lists `unseen` (endpoint actors whose model no gateway lists).
  A create or update that would take a model another enabled actor holds is
  planned as `refused` (`model_taken: <holder> (disable it first)`).
- The dry run lists the plan and writes nothing; `--apply` writes each actor
  (audited, naming you) and lists `applied` and `failed`. Exit 1 if any failed.

**`actors references ID`** lists each workflow step and rule naming the actor,
by action (`params.actor`) or by placement (`placement.actor`), with the step
path and whether the referrer is enabled. Read-only.

**`actors disable ID [--force]`** is refused (409 `actor_in_use`, exit 1,
listing each referrer) while an enabled workflow or rule names the actor, by
action or by placement. Disabled and deleted referrers do not count. The dry
run shows the enabled referrers as `in_use`. `--force` disables it anyway.

**`workflows repoint-actor WF --from A --to B [--expected-revision N] [--force]
[--apply]`** rewrites every action step in WF whose `params.actor` is `A` to
`B`, and saves the workflow. `B` must exist and be enabled. Without
`--expected-revision` the CLI reads the workflow's revision first; a stale one
is 409 `revision_conflict`. Descriptions and notes are not changed, and a step
*placed* on `A` is listed as a warning and left where it is. A workflow with no
step naming `A` is refused. **A trusted workflow is refused (409
`workflow_trusted`) without `--force`**: the repoint changes its digest, so
trust must be re-granted afterwards; the result names the digest it drops and
the new one.

## The model rule

One **enabled** endpoint actor per model. An endpoint actor is an `agent` actor
with a non-empty `params.base_url`; a disabled or deleted actor holds nothing.

- Creating or updating an enabled endpoint actor, or enabling one, whose
  `params.model` another enabled endpoint actor holds is refused: 409
  `model_taken`, naming the holder. Saving the actor disabled is fine.
- At run time `llm.complete` refuses a step whose actor shares its model with
  another enabled endpoint actor (`model_taken`).
- Actors without `base_url` are outside the rule: `qwen-fixer` and
  `qwen-reviewer` are mesh-reached agents and both hold the model `cortex`.

The checks run before the write, outside its transaction: they are operator
guidance, not a store invariant.

## The one-time newspaper move

The bundle `docs/rules/newspaper` now runs its `summary` step on the actor
`cortex-spark2` (model `cortex-spark2`, pool `qwen-spark2`). The live store
still has the earlier actor `newspaper-llm`, which holds the same model
`cortex-spark2` and which `daily-newspaper` names. The model rule means the new
actor cannot be enrolled while `newspaper-llm` is enabled, and `newspaper-llm`
cannot be disabled without `--force` while the workflow names it. So the order
is: disable the old actor with `--force`, enrol, repoint, check. Between steps 2
and 4 the `summary` step has no working actor, so do the move well before the
05:00 run.

Run it from the operator's shell with the admin token injected from the hidden
grant (see `docs/operations/rollout.md`, "Before you start"); `cr` below is that
wrapper. Read each dry run before its `--apply`, and stop at the first refusal.

1. Set the allowlist (admin): dry run, read it, then apply.

   ```bash
   cr variables set model_gateways --value '["spark=http://127.0.0.1:8001"]'
   cr variables set model_gateways --value '["spark=http://127.0.0.1:8001"]' --apply
   ```

2. Free the model. The dry run shows `in_use` (the workflow); `--force` is
   needed because `daily-newspaper` still names the actor.

   ```bash
   cr actors disable newspaper-llm --force
   cr actors disable newspaper-llm --force --apply
   ```

3. Enrol the two spark lanes (`cortex-spark`, `cortex-spark2`). Check the plan
   lists exactly those two as `create` and nothing `refused`; if the gateway
   serves another `cortex-*` model, stop and narrow the glob. The default key is
   `grant:LOBES_GATEWAY_API_KEY`; the pool `qwen-spark2` is set for
   `cortex-spark2` so it shares the PR fixer's pool.

   ```bash
   cr actors enrol-models --gateway spark --match 'cortex-*' \
     --pool cortex-spark2=qwen-spark2
   cr actors enrol-models --gateway spark --match 'cortex-*' \
     --pool cortex-spark2=qwen-spark2 --apply
   ```

4. Repoint the workflow. The dry run lists the changed step path (the
   `summary` step) and any warnings; if it says the workflow is trusted, stop
   and decide about `--force` and the trust update.

   ```bash
   cr workflows repoint-actor daily-newspaper --from newspaper-llm --to cortex-spark2
   cr workflows repoint-actor daily-newspaper --from newspaper-llm --to cortex-spark2 --apply
   ```

5. Check that nothing enabled still uses the old actor. It stays disabled, not
   deleted; re-enabling it is refused by the model rule until `cortex-spark2`
   is disabled.

   ```bash
   cr actors references newspaper-llm
   ```

Reverse a step with the same verbs: repoint back, or disable one actor and
enable the other.

## The Actors page

The editor's Actors page offers the same flow, and hides it on an older server:
**Add models** (admin) takes gateways, globs and pool overrides, shows the
server's plan, then applies it; **Used by** (editor) lists an actor's
references and links each workflow, with **Move** to repoint; **Disable** shows
the in-use refusal naming the referrers and offers **Disable anyway**. Code:
`web/src/actors/AddModelsPanel.tsx`, `web/src/actors/UsedBy.tsx`.

The route contract is in `api/openapi.json`: `POST /actors/enrol-models`,
`GET /actors/{id}/references`, `POST /workflows/{id}/repoint-actor` and
`POST /actors/{id}/disable?force=true`. See also
[`newspaper.md`](newspaper.md).
