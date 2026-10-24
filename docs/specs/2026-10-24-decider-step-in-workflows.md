# Decider step in workflows

> Workflows can ask the mesh's decider: a decider.assess step sends a state and the questions it defines (choice, yes/no, score) to the decider on the lobes gateway and returns calibrated probabilities that a later step's condition branches on

## Audience

- Workflow authors (the operator, and agents composing workflows) who want a judgment call inside a workflow - which option, how likely, how risky - and the engine that runs the step

## Before → After

- Before: The decider is reachable only from an agent's shell through the assess skill; a workflow has no step for it, so a judgment call inside a workflow means an llm.complete prompt and parsing its text
- After: A workflow step asks the decider by naming a decider actor, gets probabilities and a pick per question as structured outputs, and a later step's when branches on them

## Requirements

- Action kind decider.assess (params actor, state, questions; optional `not_after`) POSTs {model: clef, state, questions} to `<base_url>/v1/systemone` with Authorization Bearer (the actor's grant) and `X-Lobes-Model: decider:<domain>`, the domain from the actor's params.domain (default general); the body is sent as given, the gateway forwards it byte-identical
  - honesty: the request goes to <`base_url`>/v1/systemone with the bearer from the actor's grant and X-Lobes-Model decider:<the actor's domain>; the body is {model: clef, state, questions} exactly
- The request is checked before any call: state a non-empty string, questions a non-empty object with `snake_case` keys, each with type choice|noul|score and non-empty instructions, choice and score with at least 2 criteria, no images or videos, and the serialised request under a size cap; anything else fails `invalid_request`
  - honesty: each malformed request case (empty state, no questions, a bad key, a bad type, no instructions, under 2 criteria, images, over the size cap) fails `invalid_request` with no request sent
- Outputs: answers (an object, passed through: per question {type, noul} or {type, choice, confidence, probabilities} or {type, probabilities, legend, score}), host (X-Lobes-Mesh-Member, else local), domain, and usage {`input_tokens`, `output_tokens`, `total_tokens`}; a step declares one object output port answers and a later step reads e.g. answers.risky.noul in its when
  - honesty: the outputs hold answers exactly as the reply gave them, host from X-Lobes-Mesh-Member (else local), domain and integer usage; and a later step's when on `answers.<q>.noul` runs or skips (h4 with the placeholder backticked)
- Failures, never echoing a body or the key: 5xx, 503 (`backend_unavailable`, loading), 408, 429 and network errors are `decider_unavailable` (retryable); 404 (`model_not_found`, `role_infeasible`), 401, 409, 422 and other non-2xx are `decider_rejected` naming the status and the gateway's error code; a reply without an answers object for every question asked is `decider_bad_response`
  - honesty: each status class maps to its code and retryability in a table-driven test; no failure string contains the reply body or the key
- A new agent actor decider (lobes gateway <http://127.0.0.1:8001> on spark, `api_key` grant:`LOBES_GATEWAY_API_KEY`, params.domain general, model clef, `max_concurrency` 1) is committed as data, one actor per decider lane; decider.assess joins `ENDPOINT_KINDS` so the model rule and repoint-actor checks apply to it
  - honesty: the committed decider actor reads and imports from docs/rules/decider; decider.assess is in `ENDPOINT_KINDS`, a second enabled actor with model clef is refused `model_taken`
- The editor's action picker lists decider.assess (label, params, agent actors with a `base_url`), the describe vocabulary has its phrase, and the catalog, default ports and parity tests include it
  - honesty: the editor offers decider.assess with agent actors that have a `base_url`; `test_describe`, `test_action_kinds` and `test_default_ports` cover it
- docs/actors/decider.md: the actor setup, the request shape, the three answer shapes, an example step wiring answers into a later step's when, and the request-writing lessons from the assess skill (facts in state, concrete mutually exclusive options)
  - honesty: docs/actors/decider.md exists, lints clean, and its example step validates
- Besides answers, an object output picks holds the decider's highest-probability pick per question, so a step can take it without reading probabilities: choice -> the chosen option key (the reply's choice); noul -> true when noul >= 0.5, else false; score -> the criteria key of the most probable index (ties: the lower index); a later step's when reads e.g. picks.setup == `keep_current`
  - honesty: picks is exact on fixed replies: choice copies choice, noul 0.5 -> true and 0.49 -> false, score picks the most probable criteria key with ties to the lower index

## Honesty conditions

- a workflow with a decider.assess step validates, runs on the live gateway, and a later step branches on its outputs
- the port performs exactly one request per invocation and no other side effect
- neither the outputs, the failure strings nor any log contain the state text
- docs/actors/decider.md addresses workflow authors, with a worked example a later step can copy
- as of 0.25.0 no action kind posts to /v1/systemone
- a workflow step with params.actor decider returns answers and picks, and a when on picks branches
- the live check run: success, under 5 s, every option has a probability, the branch step ran or skipped as its when said, and a second run gave the same pick

## Success signals

- A workflow run with one decider.assess step (choice, noul and score questions) on the live general domain succeeds in under 5 s, its answers carry a probability for every option, and a following step whose when reads `picks.<question>` runs or skips accordingly; the same request again gives the same pick

## Scope / boundaries

- The decider assesses; it never decides and never writes text: the step returns probabilities only, and no workflow action is taken by the port itself
- The state is forwarded across the mesh to the box hosting the domain (general: the Orin): the docs say not to put secrets or private personal data in it, and the port never logs or outputs the state

## Non-goals

- No committed workflow in docs/rules uses decider.assess yet: the step ships ready to compose, the corpus fixtures stay at 12 workflows

## Assumptions

- The decider is deterministic and fast (no sampling, `output_tokens` 0, about 0.5-2 s), so the step is idempotent and a retry with the same key replays the same answer (ReplayLedger)

## Scope exploration

- `s1` — `lobes-cli lobes/gateway/_routing.py:251-316, server.py handle_systemone, docs/decider.md`: the domain is chosen only by the `X-Lobes-Model: decider:<domain>` header (missing or bare decider = general; regex `^[a-z0-9][a-z0-9-]*$`); the gateway forwards the body byte-identical and never reads model; a domain not served locally is forwarded once to the mesh member announcing it, the reply naming it in X-Lobes-Mesh-Member
  - seeds: `c2`, `c10`
- `s2` — `lobes-cli lobes/decider_sidecar/clef_vllm.py:62,86-107,136-155,310`: the sidecar requires model, state and a non-empty questions object, each type noul|choice|score, choice/score with non-empty criteria, no images/videos; `DECIDER_MAX_MODEL_LEN` 16384 tokens, an over-long schema is 422; the response is {model, answers{...}, usage{`input_tokens`, `output_tokens`}} passed through unreshaped
  - seeds: `c3`, `c4`
- `s3` — `lobes-cli server.py:775,4220; decider_sidecar/server.py _not_ready; _pressure_policy.py:436`: errors: 404 `model_not_found` / `role_infeasible`, 503 `backend_unavailable` (Retry-After 5), 503 loading, 500 failed, 422 validation, 409 `decider_domain_conflict`, 503 `role_unverified`, 508 a second hop, 401 bad key; the decider is never shed (no 429 from pressure)
  - seeds: `c5`
- `s4` — `~/.claude/skills/assess/scripts/assess.py`: the skill's client: X-Lobes-Model header, default model clef, 120 s timeout, requires instructions on each question and at least 2 criteria for non-noul types; reads answers, usage.`input_tokens` and X-Lobes-Mesh-Member
  - seeds: `c2`, `c3`, `c4`
- `s5` — `culture_rules/node/actions/llm.py:151-519`: the port pattern to copy: injectable transport (no proxies, no redirects, capped response), grant-only `api_key` resolved at call time, `base_url` checks, `not_after` + `refuse_before_admit`, ReplayLedger replay of completed results, code: detail failures that never carry a body or secret
  - seeds: `c2`, `c5`, `c12`
- `s6` — `culture_rules/actors/model_rule.py:17,40; server/model_actors.py:42,337`: `ENDPOINT_KINDS` is (llm.complete, audio.transcribe); an agent actor with params.`base_url` is an endpoint actor and `model_taken` applies when another enabled endpoint actor has the same model; joining `ENDPOINT_KINDS` makes repoint-actor require an endpoint target
  - seeds: `c6`
- `s7` — `culture_rules/actors/limits.py:186,425-437; node/actors.py:139-176`: ActorRouter wraps every action naming params.actor in LimitedActor with that actor's `concurrency_pool` / `max_concurrency`; `tokens_of` counts output.usage.`total_tokens` against a `token_budget`
  - seeds: `c4`, `c6`
- `s8` — `culture_rules/engine/runs.py:2188-2260, model/condition.py:191-227, model/workflow.py:26-29,80, model/validate.py:947,1025`: a step's when reads only its own input ports; field paths walk nested dicts and compare numbers with < <= > >=; a missing path is false so the step is skipped silently; ports are declared statically and may be type object, so one object port answers wired into a later step is the workable design
  - seeds: `c4`
- `s9` — `culture_rules/model/describe.py:92-109,341; paragraph.py:344-358,570-591; tests/model/test_describe.py:662`: `ACTION_WORDS` needs an entry (a test lists every kind); a new action kind cannot trip the c9 paragraph fallback, which is about unknown step kinds and invalid config
  - seeds: `c7`
- `s10` — `web/src/api/types.ts:104-112, web/src/rules/ActionPicker.tsx:110-117,214-230`: the editor's action catalog is a hand-kept TS copy: the ActionKind union, the picker's params/label catalog and the actorsFor switch (llm.complete and audio.transcribe offer agent actors with a `base_url`)
  - seeds: `c7`
- `s11` — `docs/rules/newspaper/actors/cortex-spark2.json, docs/rules/pocket-transcribe/actors/stt-en.json`: both gateway actors use `base_url` <http://127.0.0.1:8001> and `api_key` grant:`LOBES_GATEWAY_API_KEY` with their own pools; no decider actor exists
  - seeds: `c6`

## Decisions

- Operator 2026-10-24: the decider domain is the actor's (params.domain, default general), never a step param; a step picks the decider lane by its params.actor, so one actor per domain; the decider actor ships in a new bundle docs/rules/decider/

## Hard questions

- Domain on the actor or on the step? One actor per decider lane (decider = general, a second actor for strands-general) keeps the lane rule; a per-step domain param would let one actor reach every domain. Which? (resolved: Operator 2026-10-24: the domain lives on the actor (params.domain); the step chooses which decider by naming the actor in params.actor)
- Where is the decider actor committed: a new small bundle docs/rules/decider/ (actor only), or beside cortex-spark2 in the newspaper bundle? (resolved: Operator 2026-10-24: a new bundle docs/rules/decider/ holding the decider actor)
