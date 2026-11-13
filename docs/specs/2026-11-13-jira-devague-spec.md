# jira-devague-spec

> Opening a Jira issue starts a devague conversation in its comments: the engine asks scope, think and challenge questions, I answer in Jira, and a Fable 5.2 agent writes the converged devague spec, which lands in the library for my review.

## Audience

- The operator (Ori), who files ideas as Jira issues and answers in Jira, and who reviews the resulting spec in the library.

## Before → After

- Before: Turning an idea into a devague spec needs an interactive Claude Code session at a terminal: /scope, /think, /challenge are run by hand, and a Jira issue only gets an echo comment from culture-rules.
- After: Filing a Jira issue that names a repo is enough: the conversation runs in the issue's comments over as many rounds as it needs, and a converged, exported devague spec (with its obligations) appears in the library as a private spec item.

## Why it matters

- Specs can start from anywhere Jira is reachable (phone included), keep the anti-fabrication gate (every proposal adjudicated by the operator), and land where the operator already reads.

## Requirements

- A read-only Jira action kind (e.g. jira.read) returns an issue's description and its full comment thread as text: today the event carries only key/project/summary/status/assignee/url, `comment_body` is cut at 500 chars and an ADF body is JSON-dumped (extensions/jira/hooks.py:68,146-160,209-213), and JiraClient.`get_issue` fetches no description (client.py `_FIELDS`).
  - ⚠ contested by `d4` (risky): jira.comment refused every issue with an empty projects list while docs/actors/jira.md step 6, hooks.`_allowed_keys` and jira.read accept all
  - ⚠ contested by `d5` (acceptable): operator chose to fix the final review's findings before opening the PR
  - honesty: The read action returns the full description and every comment's full plain text (ADF flattened), with no 500-char cut, and is refused outside the actor's projects before any secret is read.
- jira.comment can post a multi-line, structured comment (numbered questions with their ids): today `add_comment` sends one ADF paragraph with one text node (extensions/jira/client.py `add_comment`).
  - honesty: A comment of several numbered lines renders in Jira as separate lines/list items, not one run-on paragraph.
- The conversation is a chain of rule firings, not one waiting run: jira.issue.created starts round one, and jira.comment.created on the same issue (`concurrency_key` like jiraconv:{trigger.data.key}) runs the next round; the wait step is time-only (model/validate.py:686-740) and a human ask is answered only through the API (actors/human.py:166), with no Jira bridge.
  - honesty: Every round is a separate run started by an event; no run stays waiting between the operator's replies, and two replies during one round still both reach the next round's thread read.
- Only allowlisted answerers advance the conversation: Jira comments by the operator's Jira user or a listed bot account; the culture-rules Jira account (the actor's `self_identity`) is never an answerer. Today Jira has no trigger filters (none registered for jira.\*) and the author's accountId is not in event data (hooks.py:282-284 uses it only for `self_authored`), so the author must reach the event and be filterable.
  - ⚠ contested by `d3` (acceptable): the build surfaced two hand-written editor lists (web/src/actors/app-config.ts:478, web/src/rules/TriggerPicker.tsx) that would leave segment and the jira users filter CLI/MCP-only
  - honesty: A comment by an account not on the answerer list (including the culture-rules account, and culture-nodes which shares it) starts no round.
- A registered runner command can keep durable per-key state across runs: machine.command runs registered argv in a fresh temp cwd with a minimal env (actors/code.py `_sandboxed`), so durable state is an absolute path argument built from a validated event field (e.g. trigger.data.key, matched by `ISSUE_KEY_RE` in hooks.py); the directory layout is the configured wrapper's business, not culture-rules'.
  - ⚠ contested by `d3` (acceptable): the build surfaced two hand-written editor lists (web/src/actors/app-config.ts:478, web/src/rules/TriggerPicker.tsx) that would leave segment and the jira users filter CLI/MCP-only
  - honesty: A registered command whose argv takes {key} from trigger.data.key cannot be steered to another path by comment text, and a key failing `ISSUE_KEY_RE` never reaches the command.
- A workflow step can bind a bridge agent actor (any model, here Fable 5.2 by configuration) in either of two generic modes: (a) output-only, read-only on the repo, its output plain step output that later steps (e.g. a registered machine.command) consume; (b) operating, the bridge running on core with tools and a writable working path given by the step. The bridge needs repo, `head_branch` and a commit `head_sha` (actors/agent.py:349,703-808) and pins sandbox/mode per actor (agent.py:715-725).
  - honesty: The agent cannot write to the repo (sandbox locked read-only) and completes a round through the bridge callbacks within its timeout.
  - honesty: In mode (b) the agent can write only under the step's configured working path; any write outside it, and any write to the repo checkout unless the actor allows it, is refused.
- library.push can publish a md file from a runner-produced path as kind spec, private: culture-library already has kind spec (`culture_library`/core/models.py:12-25); the library actor's kinds and its key gain spec by configuration (docs/actors/library.md); no repodocs route.
  - honesty: The library item's bytes equal the exported spec-md and its kind/visibility are spec/private; nothing else publishes it.
- Generic repo context for a bridge step: a Jira read can return configured fields (a custom field naming the repo) so a workflow can map it, and a step can resolve a repo's default branch and head sha; what to do when the repo is missing is configuration.
  - honesty: A workflow maps a Jira custom field's value into a bridge step's repo input and gets a 40-hex `head_sha` for that repo's default branch without any flow-specific code.
- Cross-repo (cultureagent): the Claude bridge gains an operating role for mode (b): the checkout stays unchanged (the reviewer's baseline check), Bash is limited to named commands, and one per-invocation writable working path is allowed, plus a model name (Fable 5.2). Today it has only read-only (no Bash, no writes, checkout change fails the run) and workspace-write (edits the worktree, never pushes) roles, its tool lists are per deployment (`fixer_allowed_tools`, config.py:211-212), and its only writable path is `worktrees_root` (cultureagent bridges/claude/backend.py:1-25, README.md:38-43).
  - honesty: A bridge invocation given a working path can create and edit files there, and cannot write outside it or its worktree.
  - honesty: In the operating role, an attempt to change the checkout or write outside the given path fails the run; the named command can write in the path.
- jira.read tags every comment and the issue itself with its author's accountId, and the author filter (c7) applies to jira.issue.created too, so a workflow can hand the agent only allowlisted authors' text: anyone with project access can open an issue or comment, and that text reaches an agent with tools on core (prompt injection).
  - honesty: An issue or comment by a non-allowlisted account starts no round, and its text never appears in the agent's input.
- The operating agent cannot adjudicate: in mode (b) only the reply step (driven by an allowlisted comment) may confirm, reject or resolve, and every agent-made record lands as a proposal. Risk: devague capture defaults to --origin user, which auto-confirms, and the agent could run confirm itself or edit the frame JSON.
  - honesty: After any agent run, every record the agent added is proposed; a confirm/reject/resolve exists only where a reply step cited an allowlisted comment.
- A failed round tells the operator: the round rule's `on_failure` action (model/rule.py:64) posts one Jira comment with the failure code, so a bridge timeout or a refused command never leaves the issue silently waiting.
  - honesty: A forced bridge failure produces exactly one Jira comment carrying the failure code.

## Honesty conditions

- A Jira issue opened in an allowed project, naming a repo, produces a library spec item without any terminal session by the operator.
- No change lands in ../devague; culture-rules only invokes its CLI with --frame and --json.
- tests/`test_extension_boundary.py` and the no-flow-code checks stay green; no brief or instruction text is added under `culture_rules`/.
- The operator, and only the operator (or a listed bot), answers.
- Today no rule or workflow drives devague from Jira (docs/rules has no such bundle).
- The exported spec passes devague converge and the library holds it as kind spec.
- Every proposal in the exported spec was adjudicated by an allowlisted answerer's Jira comment, and the spec is readable in the library without a terminal.
- The measured trial is recorded with the issue key and library item id.
- grep -ri devague `culture_rules`/ tests/ web/src finds nothing, and docs mention it only in an example under docs/ (guide), never as a built-in.
- No run of this flow pushes a branch, opens a PR or writes the repo checkout.

## Success signals

- On one real issue in SCRUM: >= 2 question rounds, every confirmed claim traceable to an operator reply, 0 comments from the culture-rules account treated as answers, and exactly 1 spec item (kind spec, private) in the library whose text equals the devague export.

## Scope / boundaries

- The devague CLI stays unchanged and is reached only as configuration: a registered machine.command on core's runner actor (argv with typed args, actors/code.py `bind_argv`) whose wrapper lives on core, outside the repo; devague calls no LLM (#20).
- No prompt text and no flow-specific code is committed: the agent's round instruction is step configuration on the live workflow (the bridge takes instruction from input/config and refuses `locked_instruction` since no locked brief ships, actors/agent.py:784-799), and the rules/workflows are configured live, as the no-hardcoded-prompts and no-flow-specific-code specs require (docs/specs/2026-11-02-no-hardcoded-prompts.md, 2026-11-06-no-flow-specific-code.md).
- culture-rules implements generics only and cannot tell what devague is: devague (its CLI, ids, obligations, answer grammar, export) is purely configuration — a registered runner command, the agent's step instruction, and live rules/workflows — never code, literals or tests in `culture_rules`/.
  - ⚠ contested by `d1` (acceptable): repodocs already names .devague paths (extensions/repodocs/port.py:197, `__init__.py`, model/`action_kinds.py`:51), so a literal no-mention-anywhere test fails on main

## Non-goals

- No in-run event-wait step is added to the engine for this; the round-per-firing chain covers it (model/validate.py wait is time-only, runs.py:2757-2772).
- This flow changes no repo and opens no PR: it ends with a spec in the library. Turning a spec into a PR is a later culture-rules flow; today culture-rules can read a PR (github.pr) and merge one behind an operator click and one-time code (github.merge) but has no kind that opens one (extensions/github/`__init__.py`:32-34).

## Assumptions

- The Jira actor already exists and already uses grant: the app actor jira-app (surface jira) holds token/`webhook_token`/`webhook_secret` as grant:NAME references resolved per call (extensions/jira/port.py, hooks.py, docs/actors/jira.md); no new actor kind and no grant work is needed, only configuration on the live actor.
- The bot's own question comments do not re-fire the reply rule: engine/matching.py:218-221 drops `self_authored` events unless `include_self`, and `hook_sink` tags `self_authored` only when the Jira actor sets params.`self_identity` (docs/actors/jira.md says it is set on rules.culture.dev).
- The challenge leg runs as model-chosen devague moves over the six lenses: /challenge is a skill, not a CLI verb (.claude/skills/challenge/SKILL.md), and its findings land through capture/interrogate/question/park/scope --origin llm.
- The anti-fabrication gate is carried by configuration plus one generic guarantee: only allowlisted authors' comments start a round (c7); mapping a reply onto proposals and calling confirm/reject is the configured agent instruction and wrapper, not culture-rules code.
- The repo named in the issue is checked out only if the bridge's repo allowlist admits it (cultureagent bridges/core/config.py:11-12, `repo_allowlist` / `repo_allowlist_prefixes`; empty means every invocation 403s); culture-rules adds no repo allowlist of its own.
- Rounds are bounded by the generic per-key attempt budget (model/rule.py:99-112, `max_attempts` on the issue's concurrency key), set by configuration, so a conversation cannot loop without end.
- Answers are new comments: an edited or deleted comment is ignored, because the receiver maps only jira:`issue_created`, jira:`issue_updated` and `comment_created` (extensions/jira/hooks.py `_EVENT_TYPES`).

## Scope exploration

- `s1` — `culture_rules/extensions/jira/ (port.py, hooks.py, client.py) + docs/actors/jira.md`: Jira app actor, jira.comment, POST /hooks/jira and three event types already exist; secrets are grant:NAME refs; the live actor is jira-app with `self_identity` set (guide's rules.culture.dev table).
  - seeds: `c2`, `c6`
- `s2` — `extensions/jira/hooks.py:68,146-160 + client.py get_issue/_FIELDS`: Event data lacks the description; `comment_body` truncated to 500 chars, ADF JSON-dumped; `get_issue` fetches no description or comments; no read action kind exists.
  - seeds: `c3`
- `s3` — `extensions/jira/client.py add_comment`: Comment body is one ADF paragraph with one text node; no lists or line structure.
  - seeds: `c4`
- `s4` — `engine/runs.py wait (2757-2772), model/validate.py:686-740, actors/human.py:166, model/rule.py concurrency_key, engine/claims.py:381`: No in-run event wait: wait is time-only, human asks answered via API only; rule chains with a per-issue `concurrency_key` are the fit, coalescing rapid firings.
  - seeds: `c5`, `c15`, `q5` (question)
- `s5` — `engine/matching.py:218-221 + events/hook_sink.py:271-280`: `self_authored` events dropped by default when `self_identity` is set; Jira has no trigger filters and the author is not in event data.
  - seeds: `c6`, `c7`
- `s6` — `../devague (store.py, cli/_commands/export.py, CLAUDE.md, README.md)`: Deterministic stdlib CLI, --json, cwd-relative .devague/ state without git, export writes `docs/specs/<date>-<slug>.md`; never calls an LLM (#20); no library/MCP API.
  - seeds: `c8`, `c9`
- `s7` — `culture_rules/actors/code.py + extensions/machine/`: machine.command runs registered argv only, fresh temp cwd, minimal env, 1M-char output cap, nonzero exit fails the step; no generic state store besides cursor/keyset/checklist.
  - seeds: `c9`, `q3` (question, resolved)
- `s8` — `.claude/skills/challenge/SKILL.md (+ devague docs/spec-contract.md:291-325 for oblige)`: challenge is a skill with six lenses routed through existing moves; oblige is a validate-delivery-seam verb, not spec-phase.
  - seeds: `c10`, `q2` (question, resolved)
- `s9` — `devague CLAUDE.md anti-fabrication gate (~38-52)`: LLM-origin claims land proposed; confirm/reject are user-only by convention, so Jira replies must be the operator's adjudication.
  - seeds: `c11`, `q4` (question, resolved)
- `s10` — `docs/specs/2026-11-02-no-hardcoded-prompts.md, 2026-11-06-no-flow-specific-code.md, CLAUDE.md No prompts in code`: Briefs live in actor params.briefs (admin); flows configured live, never committed.
  - seeds: `c12`
- `s11` — `extensions/llm/port.py (304, 801-812, 180-181), actors/model_enrol.py, actors/agent.py`: OpenAI-compat only, stream:true, `max_tokens`<=4000, no tools; no Anthropic support anywhere; bridge agent needs repo/`head_sha`; mesh message is fire-and-forget.
  - seeds: `c13`, `q1` (question, resolved), `q6` (question)
- `s12` — `extensions/library/port.py, docs/actors/library.md, ../culture-library core/models.py:12-25, docs/api/repodocs.md`: Kind spec exists in culture-library and md is ingested; the library actor and key need spec added; repodocs.sync is an alternative route (private, repo-linked).
  - seeds: `c14`, `q3` (question, resolved)
- `s13` — `../cultureagent (README.md:36-43, bridges/*/capabilities.py)`: Bridges (qwen, codex, claude) take repo+`head_branch`+`head_sha`, run in a detached worktree, return local commits, never push; `writable_paths` is only `worktrees_root`, so operating on a per-issue directory needs a bridge change.
  - seeds: `c30`, `c13`, `q8` (question, resolved)
- `s14` — `challenge pass / adjacent-systems lens: ../cultureagent bridges/claude (backend.py, config.py, claude_cli.py), bridges/core/config.py`: Bridge has a repo allowlist and two roles; read-only forbids Bash, so neither role runs devague with a writable dir; tool lists are per deployment; inherited API keys and push credentials are stripped from the child env.
  - seeds: `c30`, `c35`
- `s15` — `challenge pass / security lens: extensions/jira/hooks.py (author only for self_authored) + the operating agent`: Untrusted issue/comment text from any project member reaches an agent with tools; author tagging and filtering on issue.created as well as comments contains it.
  - seeds: `c33`
- `s16` — `challenge pass / unstated-assumptions lens: devague capture default --origin user (devague CLAUDE.md anti-fabrication)`: An operating agent can auto-confirm by omission or run confirm; enforcement must sit outside culture-rules (c28).
  - seeds: `c34`, `q9` (question, resolved)
- `s17` — `challenge pass / concurrency + lifecycle lens: model/rule.py:64,99-112, hooks.py _EVENT_TYPES`: `on_failure` and the per-key attempt budget exist generically; comment edits/deletes are not mapped; `head_sha` drift across rounds needs pinning; issue close has no cleanup.
  - seeds: `c36`, `c37`, `c38`, `c39`
- `s18` — `challenge pass / observability + rollback lens: runs in the editor, hook outcomes in health, rules ship disabled`: Clean pass: every round is a stored run with its error; disabling the two rules stops the flow and per-issue dirs are inert; no new rollback path needed. Residual: retention (v4).
- `s19` — `challenge pass / unexamined: live core deployment and the real Jira webhook`: Not examined: the live actors and bridge on core (no service token used), Jira's actual event pair and size limits (v3, v5, v6, lapse filed).

## Decisions

- Fable 5.2 runs as a full agent actor with tools and repo context (not a single llm.complete completion); the repo is named by a Jira field or required text in the issue.
- Obligations are devague oblige records filed on the frame's claims during the conversation.
- Each issue's devague state lives in its own directory on core; library.push is the only thing that publishes the spec (no repodocs route).
- Answers are Jira comments by the operator's user or a bot; the culture-rules Jira account that asks the questions can never be an answerer.
- The configured agent instruction has the agent file devague oblige records on requirement claims; culture-rules neither knows nor checks this.
- The configured workflow posts one comment per round listing the agent's ids and how to answer, and links the library item at the end; culture-rules only posts the text it is given (c4).
- Both modes: an agent step's output can drive a registered runner command (a), and the agent can also operate itself on core with a writable working path (b).
- An operating agent writes only the issue's working directory on core; the repo stays read-only. A change reaches the repo and main only as a PR through culture-rules.
- Repo context is pinned per issue: round one resolves the default-branch `head_sha` and later rounds reuse it (kept in the issue's working directory by configuration), so the agent's view of the repo does not shift mid-conversation.
- The agent-cannot-adjudicate guarantee is enforced in configuration only: the core wrapper (proposals only; confirm/reject/resolve only from the reply step; the agent's tools name only the wrapper) plus a post-run check refusing any confirmation not traced to a reply. No devague change.

## Hard questions

- What does 'add obligations' mean here? devague's oblige verb belongs to the validate-delivery seam after a build (docs/spec-contract.md:291-325, docs/skills.md:~432-445), not to scope/think/challenge; is it oblige records on spec claims, honesty conditions, or something else? (resolved: devague oblige.)
- risk: `concurrency_key` coalesces firings: several replies posted while a round runs collapse to the newest (engine/claims.py:381), so a round must read the whole thread (c3), not just the triggering comment.
- Where does each issue's frame live, and is the library the only sink? A per-issue directory on core reached by a registered devague wrapper, or a frame committed to a repo (then repodocs.sync would also ingest the spec as kind spec, private, docs/operations/repodocs.md)? (resolved: A per-issue directory on core; only library.push publishes.)
- What is the reply grammar in Jira: free-text answers that the model turns into user-origin decisions, explicit 'confirm c3 / reject c4' lines, or both — and who counts as the operator (reporter, a fixed accountId)? (resolved: Answers are Jira comments by the operator's user; the answerer is the operator or a bot, but never the culture-rules Jira account that asked the questions.)
- How is Fable 5.2 reached? (a) an OpenAI-compatible endpoint (Anthropic's OpenAI-compat API, or a lobes/proxy route) bound to llm.complete as single completions, with the 4000-token cap raised for a full spec; (b) a new Anthropic Messages API port for llm.complete; or (c) a full agent actor (cultureagent bridge / Claude Agent SDK) that drives devague itself with tools — today the bridge needs repo/`head_sha` inputs (actors/agent.py:~803). (resolved: (c) a full agent actor with tools, still with repo context; the repo comes from a Jira field or required text in the issue.)
- risk: llm.complete always streams with `stream_options`.`include_usage` and expects data: \[DONE\] (extensions/llm/port.py); an Anthropic compat endpoint may differ — unverified.
- In operating mode (b), what may the agent write: only the per-issue working directory on core, or also the repo (branches/commits/PRs)? Today the bridge's read-only actor lock forbids repo writes (agent.py:355-360). (resolved: Operating mode writes only the issue's working directory on core; the repo stays read-only (the bridge's read-only lock, agent.py:355-360). Repo changes reach main only through a culture-rules PR.)
- Who runs the devague moves, given a read-only repo sandbox and state on core? (a) the agent returns its moves as structured output and the round runs them through a registered devague wrapper on core (agent never writes); (b) the agent runs with the per-issue directory as its only writable path, so the bridge must run on core; or (c) the round copies the frame in and back out around each agent run. (resolved: Both (a) and (b): the agent returns moves as step output for a registered command, and it can also operate (run on core with a writable working path).)
- Where is 'the agent cannot adjudicate' enforced? (a) only in configuration: the core wrapper forces --origin llm and refuses confirm/reject/resolve unless called by the reply step, and the agent's tool list names only the wrapper; (b) also upstream in devague (e.g. a mode that refuses user-only moves); or (c) a post-run check by the wrapper that refuses any new confirmation not traced to a reply. Culture-rules itself cannot check it (c28). (resolved: (a) + (c): enforcement lives in configuration — the core wrapper forces agent moves to proposals and refuses confirm/reject/resolve unless the reply step calls it, the agent's tool list names only the wrapper, and a post-run check refuses any new confirmation not traced to a reply. No devague change.)

## Open parks

- [unknown_nonblocking] The live jira-app, library and agent actors on core were not read during scope: the CLI needs a service token this session did not use; the guide's rules.culture.dev table is the only source (control plane moved to core 2026-11-09).
- [unknown_nonblocking] Ops on core, unexamined: whether a Claude bridge runs on core today, its claude credential there, and the exact model id for Fable 5.2 under claude -p --model.
- [unknown_nonblocking] Retention: per-issue working directories on core have no cleanup when an issue closes or is abandoned; nothing reads Jira's resolved status.
- [unknown_nonblocking] Unverified (from memory, not read): Jira also sends jira:`issue_updated` for a new comment, so a rule on jira.issue.updated would double-fire a round; cheap probe: comment on a SCRUM issue and list the stored events.
- [unknown_nonblocking] Unverified: Jira's comment-body size limit (about 32k chars) versus a long round comment; jira.comment does not check length.
- [follow_up] A generic culture-rules action that opens a PR from a bridge agent's returned local commits (push a branch as the GitHub App, open the PR), so a change can reach main through culture-rules; github.merge already gates the merge.
