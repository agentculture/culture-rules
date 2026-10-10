# culture-rules

Rules engine for the AgentCulture mesh: **rules → conditions → workflows →
actions**, carried out by **actors** (agents, humans, code, services,
robots, …). It is a Python library, `culture_rules`, with a CLI, an HTTP
API, an MCP server and a graph-first React Flow editor that has four tabs:
**Workflows | Actors | Variables | Statistics**. The editor shows each rule
as an entry point of the workflow it starts, so there is no Rules tab.

**Who it is for.** Two readers, one system:

- **The operator** composing and supervising automation across spark, thor
  and spark2 from a browser at rules.culture.dev.
- **Mesh agents** that drive the same rules, workflows and actors through
  the `culture-rules` CLI and MCP server.

**Why.** One graphical, agent-operable place to decide when work happens,
how it flows across machines and who does it, and it keeps working when one
machine falters, so automation stops being per-host glue only its author
understands.

## The model

A **rule** says *when* work happens, and reads as a small flow:

```text
PR approved  →  base = main  →  Review PR  →  Comment
  trigger        condition       workflow      action
```

| Concept | What it is |
|---------|------------|
| **Rule** | A trigger, an optional condition, an optional workflow and a required action. A rule can follow another rule (*must run after* / *may run after*), supersede another, and consume the outputs its predecessor explicitly exports. A rule can also fire when another rule's run finishes ([run events](docs/run-events.md)). It is placed on a machine, an actor or a capability requirement, which decides where its trigger and condition evaluate. |
| **Condition** | A typed JSON predicate over the trigger and context, evaluated deterministically (never `eval()`). It is edited graphically; a CEL-style text form is the advanced view. |
| **Workflow** | Reusable *how*: typed inputs, internal variables, steps (logic, AI call, code run, actor task, loops), and explicit outputs. Each step has typed ports and its own placement, so one run can hop machines. A workflow doesn't know what triggered it. |
| **Action** | The terminal side effect of a rule: post a comment, send a message, call a service, ask a human. |
| **Actor** | *Who/what* does the work: agent, human, service, daemon, runner, robot. Actors carry capabilities, limits (budgets, concurrency) and secret *references*. They are **not** a stage in the rule chain. |
| **Machine** | An explicitly enrolled host (spark, thor, spark2): address, platform, capabilities such as `gpu`, and roles (store member, engine node, runner). |
| **Run** | One execution of a rule. Its state is persisted in MongoDB, so human steps and engine restarts are safe, and each step records its host and outcome. |

## The editor

The editor has four primary tabs: **Workflows | Actors | Variables |
Statistics**. There is no Rules tab: rules are folded into Workflows
([spec](docs/specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md)).
Rules are still rules in the engine, API, CLI and MCP; only the editor shows
them differently. Runs, history, ledger and inbox are not tabs; they appear
in context, inside a workflow or one of its entry points.

- **Workflows** lists workflows as chain cards: workflows linked by
  continuations sit on one card, each with what starts it, what it continues
  into, how it runs and what it ends with, and the number of rules it was.
  "See it as one chain" draws the chain. "Rules without a workflow" lists
  rules that have none yet; each can get a stored workflow with no steps.
  "New workflow" and "New rule" start new ones ("New rule" asks "When does
  this happen?" and gives the rule its own workflow at once). A workflow
  opens in one of three views, kept per viewer in the browser:
  - **Simple** (the default): When / Then. Each rule that starts the
    workflow is an entry point under When (trigger, condition, placement,
    attempt counting, exclusive group and priority, history, About,
    enable). Then shows what it continues
    into, "Ends here" (the chain-end action), "On failure" and "Runs" (key
    and budget). A value every entry point holds identically shows once;
    editing it saves rule by rule and reports each result, with a retry for
    any that failed.
  - **Detailed**: the steps and edges on the React Flow canvas, compact:
    each node shows its machine, enable switch and name, and one edge joins
    two connected nodes, labelled with how many wires it carries. Selecting
    a node shows its typed ports and wires, to drag new ones. A run lights
    up its path.
  - **Debug**: every port, type and reference; picking a port highlights
    what feeds it and what it feeds.

  Old `/rules` and `/rules/<id>` links redirect to the workflow that shows
  the rule.
- **Actors** is a large-type roster that expands inline.
- **Variables** holds the shared values rules read as `vars.<name>`.
- **Statistics** has one lane per enrolled machine: online state, CPU, GPU
  and memory load, what it is running, queue depth and 24h ok/failed.

The design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>, row "Chosen") is the
visual source of truth. The goal is visual composition, not an admin
dashboard:

- large type and large targets;
- minimal chrome and prose;
- smooth transitions;
- keyboard- and reduced-motion-accessible;
- raw YAML/JSON as an advanced mode only.

The editor lives in [`web/`](web/README.md). The wheel ships the built
bundle as `culture_rules/web_dist`, which the API serves same-origin, so
running it needs no Node.

## Install

The core library and CLI have **no third-party dependencies**. Everything
else is an optional extra:

| Extra | Adds | For |
|-------|------|-----|
| `culture-rules[server]` | FastAPI, uvicorn | `culture-rules serve` (the HTTP API and editor) |
| `culture-rules[store]` | pymongo | the MongoDB store (the API and `node run` need it) |
| `culture-rules[mcp]` | `mcp` | `culture-rules mcp` |
| `culture-rules[events]` | `events-cli` | the durable events-cli trigger subscription |
| `culture-rules[yaml]` | PyYAML | YAML export/import and repo-based agent actors |
| `culture-rules[backup]` | boto3 | S3 backup and restore |
| `culture-rules[agent]` | `agentirc-cli` | agent actors reached over the mesh |

```bash
pip install 'culture-rules[server,store,mcp,events,yaml]'
```

## Quickstart

Full, executed walkthrough: [`docs/demo.md`](docs/demo.md). In short:

```bash
# 1. a MongoDB replica set (TLS + auth) and the env vars; see docs/operations/replica-set.md
export CULTURE_RULES_MONGO_URI=... CULTURE_RULES_MONGO_TLS_CA_FILE=...

# 2. the API and editor (one per serving host)
culture-rules serve --port 8791

# 3. the engine node (one per host that runs steps)
culture-rules node run --host spark          # add --once for a single cycle
# optional: CULTURE_RULES_REPORT_CHANNEL=#ops posts each finished run's summary to that
# mesh channel (via `culture channel message`; logged instead when culture is not on PATH)

# 4. drive it from the CLI (writes preview until --apply)
export CULTURE_RULES_API_URL=http://127.0.0.1:8791
culture-rules machines create --apply --body '{"name":"spark","roles":["engine_node","runner"]}'
culture-rules rules list

# 5. the web editor: open the API's address in a browser
```

From a checkout, `uv sync` installs everything above, and
`uv run culture-rules whoami`, `uv run pytest -n auto` and
`uv run teken cli doctor . --strict` work as in CI.

## CLI

| Verb | What it does |
|------|--------------|
| `rules`, `workflows`, `actors`, `machines` | `list`, `show`, `create`, `update`, `enable`, `disable`, `delete`, `restore`, `purge`; rules, workflows and actors also `export` / `import`; machines also `drain` / `undrain`; rules also `run`, `replay` and `stop-runs` (cancel a disabled rule's current runs; a disable reports them). |
| `runs` | `list`, `show`, `cancel`, `pause`, `resume`, `controls`. |
| `serve` | Run the HTTP API (needs the `server` extra). |
| `node run` | Run this host's engine node (`--once` for one cycle). |
| `mcp` | Serve the same verbs as MCP tools over stdio (needs the `mcp` extra). |
| `whoami`, `learn`, `explain <path>`, `overview`, `doctor`, `cli overview` | Identity and self-description for agents. |

Every command supports `--json`. Results go to stdout, and errors and
diagnostics go to stderr; the two are never mixed. Exit codes: `0`
success, `1` user error, `2` environment error, `3+` reserved. **Writes are
dry-run by default**, and `--apply` commits them. Deletes are soft
(restorable); `purge` is admin-only. The CLI is a client of the HTTP API
(`CULTURE_RULES_API_URL`, with `CULTURE_RULES_TOKEN` for a service token),
except `node run`, which is the engine and talks to the store directly.

## MCP

`culture-rules mcp` exposes the same verbs as MCP tools, with the same
dry-run default, so an agent can do anything the operator can do from the
CLI. A parity test keeps the two surfaces identical.

```json
{ "mcpServers": { "culture-rules": { "command": "culture-rules", "args": ["mcp"],
  "env": { "CULTURE_RULES_API_URL": "http://127.0.0.1:8791" } } } }
```

## Architecture

```text
 browser (Workflows | Actors | Variables | Statistics)        mesh agents
        │  Cloudflare Access SSO                      CLI / MCP (stdio)
        ▼                                                    │
 cloudflared ──► loopback listener ┐                         ▼
                                   ├─► HTTP API ◄── service-token (LAN) listener
 api/openapi.json (the one contract; web/src/api/types.ts is checked against it)
                                   │
                                   ▼
        MongoDB replica set (rules, workflows, actors, machines, runs, events)
                                   ▲
 engine node on each host ─────────┘   heartbeat, ingest (events-cli), evaluate
 (spark primary; thor, spark2 redundant) placed rules, drive steps, report
```

- `culture_rules` is the library: model, engine, actors, events, machines,
  store, io. The CLI, API, MCP server and node are thin layers over it.
- The API contract is pinned in [`api/openapi.json`](api/openapi.json), and
  CI diffs the served schema against it. The JSON Schemas for the model are
  in [`schemas/`](schemas/).
- Multi-host safety: each firing and step is claimed by exactly one host, so
  redundancy never double-executes an action.
- Secrets are references (`grant:<NAME>`), never values in a definition,
  export or log.

## Operations

- [`docs/operations/replica-set.md`](docs/operations/replica-set.md): the
  MongoDB replica set across hosts (TLS, auth, the orin third voter).
- [`docs/operations/backup.md`](docs/operations/backup.md): S3 backup,
  schedule and the restore drill.
- [`docs/operations/rules-culture-dev.md`](docs/operations/rules-culture-dev.md):
  the rules.culture.dev tunnel, Cloudflare Access and the loopback listener.
- [`docs/operations/pr-fixer.md`](docs/operations/pr-fixer.md): the PR
  fixer machine (spark2): the unprivileged account, the agent bridges and their
  actors.

## Background

culture-rules is built to replace scattered per-host automation: YAML/CEL
declarations on thor, cron jobs and ad-hoc scripts per host, and agents
nudged by hand over IRC. The spec
([`docs/specs/2026-10-03-culture-rules-engine-editor.md`](docs/specs/2026-10-03-culture-rules-engine-editor.md))
records what was found and cites its scope entries:

- **s7, s8, s9:** culture-nodes is a durable workflow orchestrator whose
  declarations are trigger-condition-action rules with a CEL condition. It
  is the closest precedent. culture-rules stays a separate engine, with no
  runtime dependency on it.
- **s10, s11:** its Postgres actors table and provider-neutral actor
  protocol are the precedent for a DB-based actor registry.
- **s12, s13, s14:** the mesh knows spark, thor and orin but not spark2, and
  liveness is not enrolment, so machines are explicit enrolled records.
- **s15:** `AgentClient` is the semver-tracked way to reach agent actors
  over the mesh.
- **s25:** cultureflare documents no origin-side validation of Access
  tokens, so validating them is this repo's job.

The product model and UX come from issues
[#1](https://github.com/agentculture/culture-rules/issues/1) (the build
brief) and [#2](https://github.com/agentculture/culture-rules/issues/2)
(the product model), and the tab decision is recorded in the spec.

## Prompt files by harness

Four harnesses read four root files, with no shared base. Each file is
read by exactly one harness, and there is intentionally **no `AGENTS.md`**:

| Harness | File(s) |
|---------|---------|
| Claude Code | [`CLAUDE.md`](CLAUDE.md) (the mesh-resident prompt, and the fullest write-up) |
| Pi / associate | [`AGENTS.override.md`](AGENTS.override.md) + [`.pi/SYSTEM.md`](.pi/SYSTEM.md) |
| colleague | [`AGENTS.colleague.md`](AGENTS.colleague.md) |
| Qwen Code | [`QWEN.md`](QWEN.md) |

There are two separate selections over this clone:

- **The interactive harness:** whichever binary you run. All four are live
  at once.
- **The mesh resident:** the `backend` that `culture.yaml` declares, here
  `claude`.

See [`docs/harness-selection.md`](docs/harness-selection.md) and
[`docs/automation-contract.md`](docs/automation-contract.md).

## Skills

`.claude/skills/` vendors 19 skills (cite-don't-import), including the
devague workflow (`scope` → `think` → `spec-to-plan` →
`assign-to-workforce`), which drives the build. See
[`docs/skill-sources.md`](docs/skill-sources.md) for provenance and the
re-sync procedure.

## Contributing

See [`CLAUDE.md`](CLAUDE.md) for the conventions:

- every PR bumps the version;
- PRs go through the `cicd` lane;
- worktree layout and memory discipline.

## License

Apache 2.0 — see [`LICENSE`](LICENSE).
