# Handoff: editor fold (rules folded into workflows, three views)

Read this first after a compaction. Status at 2026-10-09 ~13:40 IDT.

## The request (operator's words)

- "Let's add a switch for seeing the flow, to seeing the variable connections. I want both for
  different visions. (On canvas)". The operator chose design canvas first, then the editor,
  on the Workflows screen.
- "review a merge rules into workflows ... the functionality added to the workflow tab, so rules
  is not needed" (not a naive 2-in-1).
- "Folded rules->workflow looks very good. I like 'Folded · Workflow with When / Then' as the
  'Simple view'. 'Workflows · Simple view' > Should be the details view. 'Workflows · Debug
  view' is great. The list & chain are great."
- Scope: "Editor only". Order: "Spec first, then build", after d26 shipped (it has: 0.16.1 live).
- Then: "think for the changes we already started designing", "Add obligations and commit, so I
  can review", "confirmed all /spec-to-plan".

## Where everything is

- **Worktree** `../.worktrees.culture-rules/editor-fold`, branch `rules/editor-fold-spec`
  (local only, not pushed), on top of `origin/main` 9d72b1e (0.16.1).
- **Commits**:
  - `38d4822`: the draft frame;
  - `9bf0f68`: the rigorous `/challenge` pass;
  - `b12a4ee`: obligations o1-o20;
  - `c329005`: the converged spec;
  - `688a994`: the converged plan;
  - `1d682e5`: the split plan, awaiting gate 2;
  - plus this handoff.
- **Spec**: `docs/specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md`; frame
  `.devague/frames/editor-rules-folded-into-workflows-three-views.json`. Read it with
  `devague show`, and the obligations with `devague oblige --list`.
- **Plan**: `docs/plans/2026-10-09-editor-rules-folded-into-workflows-three-views.md`, 12
  confirmed tasks in 6 waves. Read it with `devague plan show` and
  `devague plan waves --json` (the per-task briefs, to be quoted verbatim).
- **Split (gate 2, NOT yet approved)**:
  `docs/plans/2026-10-09-editor-rules-folded-into-workflows-three-views-split.md`.
- **Design canvas** (visual source of truth): <https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>
  v11.
  - Row "Workflows: simple ↔ detailed ↔ debug": Fold-Editor = **Simple**,
    WF-Flow = **Detailed**, WF-Variables and WF-Variables-Port = **Debug**.
  - Row "Workflows list and chain (rules folded in)": Fold-List and Fold-Chain.
  - Tabs: Workflows | Actors | Variables | Statistics.
  - Read boards with the Artifact tool `read_file` (`project/<Board>.dc.html`).

## Decisions (all confirmed by the operator)

- **c2**: editor only; no engine, model, API, CLI or MCP change. The one stored change is D7.
- **c3, c4, c5**: the three views; the list and chain as drawn; four tabs, no Rules tab.
- **D1**: one entry point per workflow; an entry notes when the same event also starts another
  workflow.
- **D2**: a continuation is owned and edited as an entry point of the workflow it starts; the
  previous workflow's Then shows a read-only Continues-into link.
- **D3-D6** (order and limits, placement, chain-end and failure actions, guard conditions):
  **shared when identical**. A value that every entry point's rule holds identically shows
  once at workflow level, and editing it writes every rule. A differing value shows per
  entry, flagged as an override.
- **D7**: a rule with no workflow gets a real stored workflow with no steps. Its trigger and
  condition are the When, its action the Then. It is created through the existing endpoints.
  Live there is one such rule: `test-jira-scrum21-to-discord` (disabled).
- **q8 / c33**: agent-state keeps tab `rules` and `AgentRulesState` as a deprecated alias for
  one release; `workflows` carries the entry-point state.
- **Challenge findings c26-c32** (all confirmed):
  - chain links come only from the `data.workflow_id` compare;
  - re-read each rule before a fan-out write (rule PUT is last-wins; there is no If-Match);
  - id, name, description and enabled are never shared;
  - history, describe and stop-runs stay per entry point;
  - D7 two-write rollback and orphan handling;
  - five docs move with the fold;
  - warn before saving a trusted workflow.
- **Deferred**: c8 and h3 (context, nothing to build). **Parked**: v1 (live rule shapes not yet
  designed against). **Plan risk** r1: the shared fake API `web/src/rules/fake-api.ts`.

## The plan's waves and the proposed split

| Wave | Tasks | Proposed owner (reviewer ≠ implementer) |
|------|-------|------------------------------------------|
| 1 | t1 engine proof (stepless workflow ≡ no workflow; test-only), t2 test inventory, t3 fold model (`web/src/fold/model.ts`), t4 fold writes (`web/src/fold/writes.ts`), t5 view switch + Detailed + Debug | Codex: t1, t3, t4. Opus subagent: t2, t5 |
| 2 | t6 Simple view, t7 list + chain | t6 Opus subagent; t7 Codex |
| 3 | t8 shell, routing and agent-state alias | main agent |
| 4 | t9 folded tests, t10 docs and harness files | t9 Codex; t10 Opus subagent |
| 5 | t11 boundary check, minor version bump, PR | main agent |
| 6 | t12 live verification + D7 conversion (needs the operator's deploy go-ahead) | main agent |

Codex builds are reviewed by Opus; Opus builds are reviewed by Codex. Codex hit usage limits
twice on 2026-10-09: fall back to an Opus subagent and a Codex review afterwards.

## Next steps

1. Get the operator's go/no-go on the split (gate 2).
2. Wave 1 fan-out: one worktree per task under `../.worktrees.culture-rules/` (branches
   `rules/fold-t1` etc.), briefs quoted verbatim from `devague plan waves --json`, test-first,
   TDD-gated merges into `rules/editor-fold-spec`.
3. If t1 finds the engine treats a stepless workflow differently, STOP and `/deviate` (D7
   depends on it). Never patch the engine silently: the spec says no engine change.
4. After wave 6: `/validate-delivery`, `/summarize-delivery`.

## Constraints that still hold

- Never touch live Mongo or port 27017 from tests.
- Secrets only through `grant`; never print values.
- Sign posts `- culture-rules (Claude)`.
- Every PR bumps the version and goes through the cicd skill.
- Ask before node changes and deploys.
- `runs pause` before an engine upgrade.
- Don't push to a PR while a fixer run on it is active.
- The live fixer runs on every same-repo, non-draft PR, including this repo's.

## Related, outside this spec

- PR fixer: 0.16.1 live; the handoff is on branch `rules/handoff`
  (`.devague/handoff-pr-fixer-rule.md`, evidence log `.devague/evidence-log-pr-fixer.md`,
  latest `17252bd`).
- Issues: #24 (hands-free webhook rotation), #25 (expiry reminders), #26 (a rule that
  visualizes a spec on its PR; this spec branch is its first user).
