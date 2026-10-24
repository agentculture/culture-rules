# Delivery Summary — Decider step in workflows

plan: `decider-step-in-workflows` · run: `complete` · date: `2026-10-24`
baseline: `devague summary skeleton`

## Intent

> Workflows can ask the mesh's decider: a decider.assess step sends a state and the questions it defines (choice, yes/no, score) to the decider on the lobes gateway and returns calibrated probabilities that a later step's condition branches on

Built on `rules/decider-assess` (0.26.0), stacked on `rules/transcript-reader`. Tasks t1–t4
are delivered. t5's live check ran against the real gateway, first from a local node. After
the operator approved the rollout, 0.26.0 (`35ed23d`) went out to all four nodes, and the
same check passed on the deployed engine. Every task is delivered.

## Planned Work

Quoted verbatim from the `devague summary` skeleton:

- `t1` — decider.assess port: `culture_rules`/node/actions/decider.py
- `t2` — Register decider.assess and commit the decider actor bundle
- `t3` — Editor: decider.assess in the action picker
- `t4` — Docs, harness files and version for the decider step
- `t5` — Live check: the decider step in a real workflow run

## Actual Delivery

| Plan task | Status | What actually landed |
|-----------|--------|----------------------|
| `t1` | delivered | `culture_rules/node/actions/decider.py`. It covers the request (`/v1/systemone`, the bearer from the grant, `X-Lobes-Model: decider:<domain>`), the pre-send checks, `answers` and `picks`, `host`, `domain`, `usage`, the error table, the ReplayLedger, `not_after`, `model_taken`, and its own transport returning headers. 110 tests. |
| `t2` | delivered | The catalog entry and default port, the describe phrase, `ENDPOINT_KINDS` and the explain text. Also `docs/rules/decider/actors/decider.json` (domain `general`, model `clef`, pool `decider-general`), plus `tests/rules/test_decider_bundle.py` and the flow test `tests/node/test_decider_flow.py`. |
| `t3` | delivered | "Ask the decider" in the editor's action picker, offering agent actors that have a `base_url`. The state is a textarea. 4 vitest cases. |
| `t4` | delivered | `docs/actors/decider.md` and its index row, the four harness files, and 0.26.0 (with `release.json` and `uv.lock`). |
| `t5` | delivered | The live check ran on a local node with the real port and the real gateway: 2 runs, both passed. After the approved rollout, the `decider` actor was imported, and a throwaway workflow made with `workflows create` ran twice on the deployed engine. Both runs passed (e14), and the workflow was then deleted and purged. |

## Mid-work Decisions

- The operator's decision c13: the domain belongs to the actor, and a step picks the decider by its actor. The actor ships in a new bundle, `docs/rules/decider`.
- The operator's caveat c14: a `picks` output with the decider's top pick per question.
- `d1` (approved) — t5's pre-PR live check runs on a local node; the engine check waits for a rollout. Decider (domain `general`): `local_now_engine_after_merge` 0.59, `ask_then_rollout` 0.37, `rollout_branch_now` 0.04. Logged in culture-rules#83. The operator then approved the rollout ("Approve rollout"), and the engine check ran on 0.26.0 from the unmerged branch head `35ed23d`.
- t1 made reply checking stricter than c19 states (delta b1, approved). A reply choice outside the asked criteria, or a legend whose size differs from the criteria, is `decider_bad_response`.
- The engine check found that `docs/actors/decider.md` gave the import as `rules import docs/rules/decider`, which fails "nothing to import" because the bundle holds only the actor. The guide now says `actors import`, which is what the rollout used.
- The scope probe of the live gateway confirmed the score shape: `legend` and `probabilities` keyed by string index. The scope survey had only inferred it.
- `d2` (approved) — the operator's `/code-review medium` of #85 found two defects, and the operator asked for them fixed ("Please fix all 8"):
  - the model rule now holds a decider by model and domain (`clef@general`), so the guide's one actor per domain works; a second actor for the same domain is still refused `model_taken`;
  - a reply nested deeper than `json` can parse fails `decider_bad_response` instead of raising.

  The version is 0.26.1, so the fixed code rolls out under its own version (0.26.0 is live from `35ed23d`).

## Drift From Plan

| Plan item | Reason for divergence | Classification |
|-----------|-----------------------|----------------|
| `t5` (`d1`, approved) | rollouts of the shared engine need the operator; the live check ran on a local node first, then on the engine after the approved rollout | acceptable |
| `t1`, `t2` (`d2`, approved) | the review fixes: the model key with the domain, and the nested-reply guard | acceptable |
| `t1` | stricter reply checks than c19 states (b1) | acceptable |

## Evidence

- tests: the full suite at `ba7e8e5` had 7192 passed and 1 failed. The failure was a local `web/node_modules` symlink; after removing it, `tests/packaging/test_repo_config.py` passed 5 of 5.
- At `33af3bb`, the evidence files passed:
  - `tests/node/test_decider_assess.py`, `tests/rules/test_decider_bundle.py` and `tests/node/test_decider_flow.py`: 119 passed;
  - `web/src/rules/ActionPicker.decider.test.tsx`: 4 passed.
- After the rebase onto the transcripts fixes: 1783 passed across both features' test files.
- lint: black, isort, flake8, bandit and scan-secrets are clean; markdownlint is clean; `teken cli doctor --strict` is green; harness-smoke is 6 of 6.
- live (local node, real `DeciderAssessPort`, real lobes gateway, the committed `decider` actor):
  - 2 runs, which succeeded in 0.88 s and 0.94 s;
  - both answered by `orin` (mesh-forwarded), and every option has a probability;
  - `go` ran, and `hold` and `careful` were skipped;
  - the picks were the same both times.
- live (deployed engine 0.26.0, `rollout verify` passed): runs `run-bb18006ce01f4495aab396f1dd87601e` and `run-717d7af467c64bf5aff27885dfdeac2c`.
  - Both succeeded, in 3.4 s and 2.4 s end to end. The `decider-general` pool ran them one at a time.
  - The ask step ran on spark and was answered by `orin`, domain `general`.
  - Every option has a probability, and the picks were the same both times.
  - `go` ran, and `hold` and `careful` were skipped.
- devague: obligations o1–o13 (approved), evidence e1–e14 and delta b1 (approved by the operator).
- commits: `9934b19..` on `rules/decider-assess`.
- issues: culture-rules#83 (the decisions log).

## Delivery Claims

| Claim | Confidence | Evidence |
|-------|------------|----------|
| The step sends the exact request with the actor's key and domain header | high | e1 |
| Malformed requests are refused before any call | high | e2 |
| Outputs carry `answers` unchanged, `picks`, `host`, `domain` and `usage` | high | e3, e4 |
| Errors map to codes and retryability without leaking the body, key or state | high | e5, e7 |
| A non-Clef reply fails without partial picks | high | e6 |
| The decider actor imports, and the kind is an endpoint kind with `model_taken` enforced | high | e9 |
| The editor and the catalog offer the kind | high | e10 |
| A later step's `when` on `picks` runs or skips accordingly | high | e11 (flow test on a real Node) |
| It works against the live decider: under 5 s, the same pick twice | high | e12 (local node) and e14 (the deployed engine) |
| The guide warns about missing fields | high | e13 |
| The step works in a workflow run on the deployed engine | high | e14: 2 engine runs of a throwaway workflow, both succeeded |

## Remaining Work / Follow-up

- A second decider actor (for example `strands-general`) needs its answer shape supported first (c19). Later work.
