# Build Plan — Decider step in workflows

slug: `decider-step-in-workflows` · status: `exported` · from frame: `decider-step-in-workflows`

> Workflows can ask the mesh's decider: a decider.assess step sends a state and the questions it defines (choice, yes/no, score) to the decider on the lobes gateway and returns calibrated probabilities that a later step's condition branches on

## Tasks

### t1 — decider.assess port: `culture_rules`/node/actions/decider.py

- instruction: Model the module on `culture_rules`/node/actions/llm.py (transport type, `_NoRedirect` opener, grant-only `api_key` via actors.secrets.resolve, `base_url` checks, `_Refused`, ReplayLedger, `refuse_before_admit`, `load_bound_actor`, `senior_holder` `model_taken`) but write a fresh module; the live reply shapes are in the frame's scope entry s12 (score: probabilities keyed by string indexes, legend index -> criteria key). Do not edit `action_kinds.py`, runner.py, describe.py or `model_rule.py` (t2 owns them).
- covers: c2, h2, c3, h3, c4, h18, c5, h5, c9, h9, c10, h10, c14, h11, c19, h19
- acceptance:
  - DeciderAssessPort POSTs exactly {"model": "clef", "state", "questions"} to `<base_url>/v1/systemone` (no doubled /v1) with Authorization Bearer from the actor's grant and `X-Lobes-Model: decider:<domain>` from the actor's params.domain (default general); one request per invocation, no other side effect
  - `invalid_request` before any request for: empty state, missing/empty questions, a key not matching ^\[a-z\]\[a-z0-`9_`\]\*$, a type other than choice|noul|score, missing instructions, choice/score with fewer than 2 criteria, images or videos, a serialised request over the size cap
  - outputs: answers exactly as replied, picks (choice -> choice; noul -> noul >= 0.5; score -> legend\[most probable index\], ties to the lower index), host (X-Lobes-Mesh-Member, else local), domain, usage {`input_tokens`, `output_tokens`, `total_tokens`} as integers
  - a table-driven test maps statuses: 5xx/503/408/429/network -> `decider_unavailable` retryable; 404/401/409/422/other -> `decider_rejected` with the status and the reply's error code, not retryable; a reply missing answers or the Clef fields of any asked question -> `decider_bad_response`, never partial picks
  - no failure string, output or log line contains the state text, the reply body or the key; `not_after`, `refuse_before_admit` and the ReplayLedger behave as in llm.complete; tests in tests/node/`test_decider_assess.py` with a fake transport

### t2 — Register decider.assess and commit the decider actor bundle

- instruction: Small edits only in the registration files; the explain catalog prose mentions endpoint kinds near catalog.py:420 - update it if it lists them.
- depends on: t1
- covers: c6, h6, c7, h7, c16, h15
- acceptance:
  - `culture_rules`/model/`action_kinds.py` has decider.assess (actor required, state required, questions required dict, `not_after`); runner.`default_ports` registers action:decider.assess; describe.`ACTION_WORDS` has its phrase; `ENDPOINT_KINDS` includes it; `test_action_kinds`, `test_default_ports`, `test_describe` and `test_model_rule` cover it
  - docs/rules/decider/actors/decider.json: agent actor decider on spark, `base_url` <http://127.0.0.1:8001>, `api_key` grant:`LOBES_GATEWAY_API_KEY`, params.domain general, model clef, `max_concurrency` 1; tests/rules/`test_decider_bundle.py` reads and imports it and shows a second enabled actor with model clef is refused `model_taken`
  - before this change no action kind posts to /v1/systemone (grep check recorded in the PR)

### t3 — Editor: decider.assess in the action picker

- instruction: TS only, mirror how llm.complete is listed; no Python files.
- covers: c7, h7
- acceptance:
  - web/src/api/types.ts ActionKind includes decider.assess; web/src/rules/ActionPicker.tsx catalog has its label and params (actor, state, questions, `not_after`) and actorsFor offers agent actors with a `base_url`; a vitest case in ActionPicker tests shows it; tsc and vitest green

### t4 — Docs, harness files and version for the decider step

- instruction: Docs describe what is on disk.
- depends on: t1, t2, t3
- covers: c8, h8, c15, h14, c20, h20
- acceptance:
  - docs/actors/decider.md: for workflow authors - the actor and its domain, the request shape, the three answer shapes and picks, an example step wiring answers/picks into a later step's when, the missing-field warning with an exists example, the request-writing lessons (facts in state, concrete mutually exclusive options), and that the state crosses the mesh (no secrets or private data); its example step validates; markdownlint clean
  - the four harness files gain the same short entry; minor version bump in pyproject.toml, uv.lock, CHANGELOG.md and docs/rules/pr-fixer/release.json; the full suite (run once, -n 4), lint, teken doctor and harness-smoke green

### t5 — Live check: the decider step in a real workflow run

- instruction: Ask the operator before rollout steps; inject the admin token, never read it; synthetic state only.
- depends on: t4
- covers: c1, h1, c17, h16, c18, h17
- acceptance:
  - after the release rolls out and the decider actor is imported, a throwaway workflow (created through the CLI, not committed) with one decider.assess step (a choice, a noul and a score question on synthetic facts) and a branch step whose when reads picks succeeds in under 5 s, every option has a probability, the branch ran or skipped as its when said, and a second run gave the same pick; the workflow is deleted afterwards
