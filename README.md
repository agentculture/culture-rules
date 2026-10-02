# culture-rules

Rules engine for the AgentCulture mesh: **rules → conditions → workflows →
actions**, carried out by **actors** (agents, humans, code, services,
robots, …). It is a Python backend library, `culture_rules`, plus a
Node.js + React Flow visual editor with three tabs: **Rules | Workflows |
Actors**.

> **Status: scaffold.** This repo was provisioned from
> `culture-agent-template`. The agent-first CLI, mesh identity, skill kit and
> CI/publish baseline are in place. The engine, HTTP API and editor are
> **planned** and not built yet. The build brief is
> [#1](https://github.com/agentculture/culture-rules/issues/1), and the
> product model and UX are
> [#2](https://github.com/agentculture/culture-rules/issues/2).

## The model (planned)

A **rule** says *when* work happens, and reads as a small flow:

```text
PR approved  →  base = main  →  Review PR  →  Comment
  trigger        condition       workflow      action
```

| Concept | What it is |
|---------|------------|
| **Rule** | A trigger, an optional condition, a workflow and/or an action. A rule can follow another rule (*must run after* / *may run after*) and consume its exported outputs. |
| **Condition** | A serialisable predicate over the trigger and context. It is edited graphically in common cases and never evaluated with `eval()`. |
| **Workflow** | Reusable *how*: inputs, internal variables, steps (sequence, branch, wait, agent/code/human work) and outputs. It doesn't know what triggered it. |
| **Action** | A concrete side effect: post a comment, transition a ticket, send a message, call a service, run code, ask a human. |
| **Actor** | *Who/what* can do the work: agent, human, service/daemon, runner, robot. Actors carry capabilities. They are **not** a stage in the rule chain. |

Human actors make runs long-running and asynchronous, so the engine will
persist run state rather than hold it in memory.

## The editor (planned)

The editor has exactly three primary tabs:

- **Rules** shows each rule as a compact graphical flow, and grows new
  rules progressively.
- **Workflows** is the React Flow canvas. Inputs, outputs and variables are
  mapped visually.
- **Actors** is the registry of actors and their capabilities.

Run history and debugging appear in context, never as extra tabs. The
design goal is a visual composition tool, not an admin dashboard:

- large type and large targets;
- minimal chrome and prose;
- smooth transitions;
- keyboard- and reduced-motion-accessible.

## Quickstart (what works today)

```bash
uv sync
uv run pytest -n auto                 # run the test suite
uv run culture-rules whoami           # identity from culture.yaml
uv run culture-rules learn            # self-teaching prompt (add --json)
uv run teken cli doctor . --strict    # the agent-first rubric gate CI runs
```

## CLI

| Verb | What it does |
|------|--------------|
| `whoami` | Report this agent's nick, version, backend, and model from `culture.yaml`. |
| `learn` | Print a structured self-teaching prompt. |
| `explain <path>` | Markdown docs for any noun/verb path. |
| `overview` | Read-only descriptive snapshot of the agent. |
| `doctor` | Check the agent-identity invariants (prompt-file-present, backend-consistency). |
| `cli overview` | Describe the CLI surface itself. |

Every command supports `--json`. Results go to stdout, and errors and
diagnostics go to stderr; the two are never mixed. Exit codes: `0`
success, `1` user error, `2` environment error, `3+` reserved. Rule,
workflow and actor verbs will follow the same contract, with writes
dry-run by default and `--apply` to commit them.

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
