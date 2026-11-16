# qodo bridge

> qodo-cli is a bridge: one app with an HTTP API, an MCP server and an embeddable web surface, built on a reusable bridge framework. culture-rules drives it through an actor, so a rule runs Qodo when a PR is opened and another has a cortex agent improve the repo's Qodo configuration when a PR is merged. No extra repo was created.

## Audience

- The operator who configures rules and workflows in culture-rules; agents (Pi first) that operate qodo-cli through its CLI or MCP server; and in-process integrators such as devex-cli (qodo-cli issue 18).

## Before → After

- Before: qodo-cli is a CLI a person or agent runs by hand under their own gh login. Qodo's findings are read by parsing the bot's comments, nothing happens when a PR opens or merges, and Qodo's configuration changes only when someone edits it. The previous bridge of this kind (jira-devague) needed a separate wrapper repo and live-only setup.
- After: qodo-cli runs as a service with an API, an MCP server and a web UI over one Python API. Two configured culture-rules flows use it: after Qodo reviews an opened PR its findings are collected and triaged, and after a merge associate proposes an improved Qodo configuration, the bridge validates it, and culture-rules opens it as a PR through its GitHub App. culture-rules' code contains no Qodo-specific line.

## Why it matters

- It sets the pattern for connecting an app to culture-rules without a wrapper repo or flow code in the engine: the app owns its API, MCP and UI, and culture-rules owns only configuration. The devague service planned to replace jira-devague is the next user of the pattern.

## Requirements

- qodo-cli gains a server (HTTP API), an MCP server and a web UI, and its command is named qodo-cli. None of the three exists today: the only network code is the urllib call in qodo/cli/`_qodo_api.py`.
  - honesty: qodo serve starts the API and the web UI from an installed wheel with no Node on the machine, and qodo mcp serves the same verbs as tools.
- The bridge framework is assembled by citing culture-rules' generic parts into qodo-cli, since no shared reference for a server, an MCP server or a web shell exists: server/static.py (SPA serving), auth/access.py, resolve.py, tokens.py and principal.py (Cloudflare Access on loopback, service tokens on LAN, roles), mcp/server.py and tools.py with the verb registry and tests/`test_surface_parity.py`, the OpenAPI pin, `hatch_build.py` and web/src/culture-design. agentfront (formerly teken) derives only a CLI, a one-tool MCP and markdown pages, with no auth and no web app.
  - honesty: Each cited file in qodo-cli carries a header naming its culture-rules source path and commit, as xteink's access.py does.
- The options Qodo's API offers beyond rules search are reviewed, and the bridge's verbs are chosen from that review.
  - honesty: The verbs chosen for the bridge each trace to an entry in the recorded review of Qodo's docs (scope entry s14), and each MCP tool is confirmed with a live tools/list call before it is built on.
- The bridge's API, MCP tools and CLI verbs are thin layers over one public Python API in qodo-cli, the qodo.api module that qodo-cli issue 18 asks for, so the three surfaces cannot drift.
  - honesty: No CLI handler, MCP tool or HTTP route in qodo-cli imports qodo.cli.`_providers` or qodo.cli.`_qodo_api` directly; they call qodo.api.
- Improving Qodo after a merge can draw on Qodo's own record: the MCP tools review-history and similar-suggestions return past suggestions and whether they were accepted, and the changes land as repo files a PR can carry (.`pr_agent.toml`, and rule files Qodo imports on push).
  - honesty: A live call to Qodo's review-history tool returns accepted and rejected suggestions for at least one of our repos.
- The bridge's first verbs cover: list a PR's findings from Qodo's MCP server, mark a finding implemented or dismiss it, search rules, read review history for a repo, and propose a configuration change, which validates the changed files and returns them for a workflow step to open as a PR.
  - honesty: Each verb has a CLI command, an MCP tool and an HTTP operation, and an explain catalog entry.
- Every configuration PR is opened with a fixed branch prefix and a label, set in the workflow's step configuration and not by the agent, and the improvement rule's condition excludes PRs carrying that mark.
  - honesty: The mark is set by the bridge, not by the agent, so an agent cannot omit it.
    - instruction: check the bundle's workflow: the open-PR step's branch and labels are literals or built from trigger data, never from the agent's output
- Text from PRs, Qodo findings and review history is untrusted input to the improvement agent. The qodo bridge's propose verb returns a change only when it touches paths in an allowlist of Qodo configuration files and the files validate, and refuses otherwise; the workflow opens a PR only from what the bridge returned.
  - honesty: A run whose checkout diff touches any other path produces no PR and a refusal the workflow can read.
- The opened-PR flow starts from created events only (github.review.submitted, github.comment.created or github.`review_comment`.created by Qodo's login) and runs once per PR head commit, however many events one review produces. Re-reviews that edit an existing comment are out of the first version.
  - honesty: One Qodo review that posts 1 summary comment and several inline comments starts exactly 1 run for that head commit.
- Bridge API responses meant for workflow steps are small: counts, ids, statuses and URLs, under the 64 KiB http.call body cap. Full findings stay in the bridge and its UI.
  - honesty: The findings verb's workflow-facing response for a PR with 100 findings is under 64 KiB.
- At most 1 open configuration PR per repo: while one is open, a new improvement run for that repo ends without opening another. Improvement runs for one repo are serialized, and each run works in its own fresh checkout.
  - honesty: Two PRs merged in the same minute on one repo produce at most 1 open configuration PR.
- Each bridge reports its own health: an unauthenticated health route with no secrets, a doctor check for the Qodo API key's presence and days to expiry, and a log of every PR it opened and every finding it changed.
  - honesty: doctor reports a failing check when the API key expires in fewer than 14 days.
- No bridge route that reads findings, changes a finding or proposes a configuration change answers without authentication. culture-rules reaches the bridge with a service token held as a grant.
  - honesty: A request without a token to each such route returns 401.
- Both flows can be stopped in one step and leave nothing behind: disabling the bundle's rules stops new runs, the bridges hold no state culture-rules depends on, and the bundle states the lowest culture-rules version it needs.
  - honesty: With the rules disabled, a PR open and a PR merge start 0 runs.
- culture-rules gains one generic GitHub action that opens a PR as the App: given a repo in the actor's allowlist, a new branch name, file paths with contents, a title, a body and labels, it commits the files to the new branch (never the default branch) and opens the PR. Its name, params and docs do not mention Qodo, and it follows the all-surfaces rules for a new action kind (catalogue fixture, docs/actors/github.md, tests).
  - honesty: The action refuses a repo outside connection.repos and a branch equal to the default branch, and a retry of the same step opens no second PR.

## Honesty conditions

- Both flows run end to end on a real PR with culture-rules holding only configuration, and no repository was created.
- pip install qodo-cli with no extras installs zero third-party packages and every existing verb still works.
- teken cli doctor . --strict stays green in qodo-cli CI with the new verbs registered.
- The rule conditions exist as configuration: `head_repo` equals `base_repo`, draft is false, `self_authored` is false, and a concurrency key per repo and PR number.
- Every capability added to culture-rules for this work is named and documented without reference to Qodo and is usable by another app.
- No file in qodo-cli or culture-rules contains the improvement agent's instruction text; it is read from actor or step configuration at run time.
- One 'associate run' with the cortex model, started by a workflow step on the machine that holds the checkout, changes a Qodo configuration file and returns JSON the next step can read.
- A test or a recorded live run shows a merged bridge-opened configuration PR starting no improvement run.
- Each named audience has a documented way in: the editor and CLI for the operator, CLI and MCP for agents, qodo.api for integrators.
- The before state matches qodo-cli main today: no listener, no MCP code, config verb limited to show, validate and init.
- The service runs as a unit on core and both flows are enabled there.
- The bridge framework parts in qodo-cli contain no Qodo-specific code, so the next app can cite them as they are.
- The grep is run in CI or recorded as evidence at delivery.
- The run id and the PR are recorded as delivery evidence.
- The two PRs and the run list are recorded as delivery evidence.
- The parity test is data-driven from the verb registry, so a new verb is covered without editing the test.
- The qodo bridge has no merge call, and a request naming a repo outside the allowlist is refused before any GitHub request.
- The existing contract tests in tests/`test_contracts.py` pass unchanged.

## Success signals

- grep -ri qodo `culture_rules`/ returns 0 matches after the work, and tests/`test_extension_boundary.py` passes unchanged in its list of apps.
  - instruction: run: grep -ril qodo `culture_rules`/ | wc -l (expect 0) and uv run pytest tests/`test_extension_boundary.py`
- On 1 real same-repo PR, one workflow run started by Qodo's review ends 'succeeded' with the findings count the bridge returned, and the App's own comments start 0 runs.
- After 1 merged PR, the improvement flow opens exactly 1 configuration PR, and merging that PR starts 0 improvement runs.
- Every bridge verb is reachable 3 ways (CLI, MCP tool, HTTP operation) and a parity test fails when 1 of the 3 is missing.
  - instruction: cite culture-rules tests/`test_surface_parity.py` into qodo-cli and run it in CI

## Scope / boundaries

- qodo-cli's runtime dependencies stay empty (pyproject.toml dependencies = \[\], `best_practices.md`:11-14). The server, MCP and web parts sit behind optional extras and are imported lazily, as culture-rules does with its server and mcp extras.
  - instruction: run the existing test suite in an environment without the server and mcp extras
- Every new qodo-cli verb keeps the existing contract (CLAUDE.md:61-64, 159-167): --json, CliError, exit codes 0/1/2, an explain catalog entry, mentions in learn and overview, and teken cli doctor --strict green. Every PR bumps the version.
- The rules guard against the PR fixer's recorded failure modes: same-repo, non-draft PRs only; the App's own comments (`self_authored`) and bot noise never start a run; one run per PR through a concurrency key; attempts are capped (docs/deliveries/2026-10-06-pr-fixer-rule.md:93, CHANGELOG.md:1107).
- culture-rules connects surfaces and can call APIs, and it does not know the word qodo: no Qodo extension package, no Qodo action kind, no Qodo event namespace and no Qodo literal in `culture_rules`/. The configuration (actors, rules, workflows) knows Qodo and defines how to work with it. Anything culture-rules lacks to call the bridge is added as a generic capability.
- No prompt lives in code. Instructions for the agent that improves the Qodo configuration are an actor brief (llm.complete) or step configuration (a bridge agent step's instruction), per CLAUDE.md 'No prompts in code' and docs/actors/llm.md:172-191.
- The configuration PR opened for an improved Qodo configuration never starts another improvement run: no loop.
- The bridges never write to GitHub repositories: no push, no merge, no PR. A configuration change reaches a repo only as a PR that culture-rules' App opens on a repo in the App actor's allowlist, and that a person reviews and merges.
- The --json output of qodo-cli's existing verbs (rules, review, config, doctor) does not change shape; devex-cli binds to it in-process (qodo-cli issue 18).

## Non-goals

- No new repository, and no further investment in jira-devague. The records call it a stopgap to be replaced by a devague service that culture-rules operates directly (docs/deliveries/2026-11-13-jira-devague-spec.md:202); the qodo bridge is the pattern that replaces the wrapper-repo approach, built inside qodo-cli and culture-rules.
- The PR fixer is not revived. Its 13,300 lines were removed in 0.43.0 because flow code in the engine made every flow change a release (docs/specs/2026-11-06-no-flow-specific-code.md).
- Qodo's server-side analysis is not reimplemented (qodo-cli docs/qodo-skills-sources.md, and the non-goals of docs/specs/2026-06-16-qodo-cli-now-does-qodo-s-two-core-jobs-natively-fr.md).
- The first version does not trigger Qodo reviews; it relies on Qodo's automatic review on PR open.

## Assumptions

- 'Run Qodo' cannot mean 'trigger a Qodo review' with today's code: qodo review list|resolve only reads, parses and resolves what Qodo's GitHub bot already posted (qodo/cli/`_providers.py`:437-454, 574), and docs/qodo-skills-sources.md says there is no reimplementation of Qodo's server-side analysis. A trigger would be new (for example a /review PR comment), and whether Qodo obeys a bot-authored command is unverified.
- 'Configure/improve Qodo' means changing .`pr_agent.toml` and `best_practices.md` in the merged PR's repo. qodo config only has show, validate and init (qodo/cli/`_commands`/config.py:375-390); nothing updates these files or learns from a merged PR today.
- No GitHub code is needed for the two triggers. 'PR opened' is github.pr.opened and 'PR merged' is github.pr.closed with data.merged true (`culture_rules`/extensions/github/hooks.py:84-96, 165-167). Repo, author and branch are rule conditions on data, since GitHub registers no trigger filter.
- Qodo's structured interface is its remote MCP server (docs.qodo.ai/agentic-toolbox/mcp, Bearer personal API key), not a REST API: no public API reference, webhooks, review-trigger API or analytics API is documented. The bridge reads findings through the MCP tools 'findings', 'mark-implemented' and 'dismiss' instead of parsing the bot's comment HTML. Tool input schemas are not in the docs and come from tools/list.
- A review is triggered by the PR comment /`agentic_review`; the legacy /review and /improve commands are being sunset. Qodo already reviews on PR open, reopen and ready-for-review when /`agentic_review` is in `pr_commands`. Whether a comment from a GitHub App account is honoured is not documented.
- Qodo's default `ignore_bot_pr` = true skips PRs opened by bots, so a configuration PR the bridge opens is not reviewed by Qodo unless the repo turns that off. The improvement loop still needs its own guard on merge.
- Part of qodo-cli's config verb may target legacy settings: it writes `best_practices.md` and \[`pr_reviewer`\] `extra_instructions`, while Qodo's current docs describe \[`review_agent`\] keys (`issues_user_guidelines`, `compliance_user_guidelines`, `inline_comments_severity_threshold`) and rules imported from AGENTS.md or CLAUDE.md on push, and do not mention `best_practices.md`.
- The rename changes the installed command only. The Python import package stays qodo, so devex-cli's in-process calls to qodo.cli.main keep working; the PyPI name is already qodo-cli.

## Scope exploration

- `s1` — `qodo-cli repo (qodo/, pyproject.toml, CLAUDE.md, best_practices.md)`: Stdlib-only CLI at 0.9.1 with verbs rules, review/pr, config, doctor and the identity verbs. No server, MCP, web or daemon code and no plan for one. Checked out on docs/readme-rewrite, clean, 10 files ahead of main.
  - seeds: `c2`, `c3`, `c4`
- `s2` — `qodo-cli verbs review and config (qodo/cli/_providers.py, qodo/cli/_commands/config.py)`: review only reads and resolves Qodo's posted comments through gh; nothing triggers Qodo. config scaffolds, shows and validates .`pr_agent.toml` and `best_practices.md`; nothing improves them from merged PRs. Qodo API key comes from `QODO_API_KEY` or ~/.qodo/config.json; GitHub access is the caller's gh login. What 'run Qodo' and 'improve Qodo' mean is routed to q1 and q4.
  - seeds: `c5`, `c6`
- `s3` — `culture_rules/extensions/github (hooks.py, events.py, port.py) and docs/actors/github.md`: Both triggers exist as events with PR facts. github.comment can post any body as the App on an allowlisted repo. Qodo's own review arrives as github.comment.created, github.`review_comment`.created and github.review.submitted when the actor's params.events lists them. The engine has no action that pushes, edits files or opens a PR.
  - seeds: `c7`, `c8`
- `s4` — `culture_rules/extensions/registry.py, builtin.py, tests/test_extension_boundary.py, model/app_actor.py`: Three ways to reach an external HTTP service exist: http.call on a service actor with params.http.allow (no new code, synchronous, 64 KiB body cap, untyped); an agent actor with `bridge_url` speaking the cultureagent bridge protocol (async, callback, no new code); or a new package under `culture_rules`/extensions/ with typed kinds, a port, an optional /hooks/ route and event types. `APP_SURFACES` is a hardcoded tuple of five. No plugin discovery exists: every extension is in builtin.py. There is no generic POST /events; events come in by webhook route or the MQTT bus.
  - seeds: `c9`
- `s5` — `jira-devague repo and culture-rules docs/operations/jira-conversations.md, docs/deliveries/2026-11-13-jira-devague-spec.md`: jira-devague is a separate stdlib CLI repo, installed as a uv tool on core, reached through runner commands and a Claude bridge operate agent. Actors, the bridge unit, grants, rules and workflows are live-only. Its safety rests on an operator-held command-prefix allowlist and env vars. 'bridge' in culture-rules today means the cultureagent agent-session bridge, not an app.
  - seeds: `c11`
- `s6` — `PR fixer records (docs/specs/2026-11-06-no-flow-specific-code.md, docs/deliveries/2026-10-06-pr-fixer-rule.md, CHANGELOG.md:131)`: The PR fixer was a PR-driven agent flow coded into the engine, switched off live on 2026-10-19 and removed in 0.43.0. Its lessons on bot noise, self-authored comments, attempt caps and same-repo PRs apply to any new PR-driven flow.
  - seeds: `c12`, `c8`
- `s7` — `qodo-cli docs/qodo-skills-sources.md and docs/specs/2026-06-16-*.md`: The first spec's non-goals exclude reimplementing Qodo's server-side analysis and an interactive login.
  - seeds: `c13`
- `s8` — `culture-rules app framework (culture_rules/server, auth, mcp, client, hatch_build.py, web/) and agentfront, culture-agent-template, citation-cli, guildmaster`: The citable parts are static.py (104 lines), auth (about 1,050 lines), mcp (about 190 lines), the verb registry with its parity test, `hatch_build.py` and the culture-design tokens. server/app.py and service.py are rules-specific. xteink already cites auth/access.py with recorded provenance and has the API, MCP and web triple; culture-library and pocketsync are smaller precedents. Only a python-cli reference exists in .teken/reference.
  - seeds: `c14`
- `s9` — `culture_rules/actors/agent.py, extensions/llm, docs/actors/llm.md, lobes-cli README`: cortex is a lobes model lane (27B Qwen), addressed as a model through the lobes gateway. llm.complete gives text only. A bridge agent step gets a checkout from repo, `head_branch` and `head_sha`, can commit, and reports commits and changed files; the operate sandbox is actor-pinned and shipped only in the Claude bridge (cultureagent 0.17.0). No committed bundle holds a bridge agent actor.
  - seeds: `c15`, `c10`
- `s10` — `embedding precedents (culture_rules/, web/src, culture-library routes_web.py:37, pocketsync web/server.py:72, irc-lens web/app.py:60)`: No iframe, postMessage, shared shell or embedding design doc exists in culture-rules or its siblings. Each app has its own hostname behind Cloudflare Access, and three siblings forbid framing with frame-ancestors 'none'. Embedding is new work, and its meaning is undecided.
  - seeds: `q3` (question, resolved)
- `s11` — `cultureagent README.md:36-43 (agent bridges)`: Three stdlib HTTP bridges ship: qwen (Qwen Code over ACP), codex and claude. They never push. No Pi bridge exists in this checkout (git log head e7f7028/0.15.1, which may be behind the 0.17.0 the Jira records cite).
  - seeds: `c15`
- `s12` — `http.call as the only link (culture_rules/extensions/http/port.py)`: Configuration-only access to the bridge goes through http.call on an actor with params.http.allow: synchronous, one request per step, response body capped at 64 KiB, loopback reachable only when that exact IP is allowlisted, auth by a grant-resolved header. There is no generic POST /events, so the bridge cannot push an event in without the MQTT bus; the Qodo-review-landed trigger is the existing GitHub review and comment events filtered by author in a rule condition.
  - seeds: `c9`
- `s13` — `qodo-cli open issues 18 and 22`: Issue 18 (from devex-cli) asks for a public Python API, qodo.api, because the useful logic sits in private modules qodo.cli.`_providers` and qodo.cli.`_qodo_api` and devex drives main() and re-parses stdout. Issue 22 is titled 'mass-update: deterministic update'; its body was not read.
  - seeds: `c21`
- `s14` — `Qodo public docs (docs.qodo.ai: agentic-toolbox/mcp, qodo-tools-reference, code-review/use-qodo-in-prs, configuration/configuration-file, governance/rule-enforcement, account-management/manage-api-keys, pricing-and-usage)`: Read through web fetch summaries, so wording is paraphrase. Documented: a remote MCP server with PR findings, mark-implemented, dismiss, rules search/list/get, merged-PR review history and codebase tools; /`agentic_review` as the trigger comment; `ignore_bot_pr` default true; .`pr_agent.toml` on the default branch, a wiki page, a pr-agent-settings repo and the portal as configuration sources; rules created in the portal, imported from AGENTS.md or CLAUDE.md, or mined from PR history; analytics as dashboard and CSV only. Not documented: webhooks, a REST reference, rules write over HTTP, check runs for a finished review, bot-posted commands. Personal API keys expire after at most 365 days; trial workspaces get 100 reviews a day.
  - seeds: `c20`, `c22`, `c23`, `c24`, `c25`, `c26`
- `s15` — `cultureagent origin/main (0.17.0, cultureagent/bridges/) and pi 1.5.2 on this machine`: Upstream has bridges claude, codex and qwen over one shared core (cultureagent/bridges/core) and no Pi bridge. pi 1.5.2 offers --print for a non-interactive run, --mode json or rpc, and 'pi mcp' to manage MCP servers.
  - seeds: `c15`, `q7` (question, resolved)
- `s16` — `associate (installed 0.9.0, the sibling checkout ../associate, README.md, 'associate run --help')`: associate is a multi-model harness on Pi. 'associate run \[prompt\] --checkout DIR --timeout S --json' runs one task headless (default timeout 900 s); its cortex model may change the checkout behind a decider safety gate, and nothing is ever pushed. The repo README also lists --model and --test-command, which the installed 0.9.0 help does not show. culture-rules has a one-shot adapter for 'colleague work' (`culture_rules`/actors/agent.py:167) and none for associate; a registered runner command could run it with no code change.
  - seeds: `c15`, `q7` (question, resolved)
- `s17` — `challenge pass / security lens: associate README (safety gate, never pushes) and the improvement flow in the spec`: An agent with write access to a checkout reads text written by PR authors and by a third-party bot, and its output becomes a PR that changes how the reviewer behaves. The spec had no human gate, repo allowlist or path limit on that output.
  - seeds: `c41`, `c42`
- `s18` — `challenge pass / unstated-assumptions lens: c24 against qodo/cli/_providers.py (gh) and parked v1`: c24 assumed a bot-authored PR. qodo-cli has no token handling and relies on the caller's gh login, so the author would be a person and `ignore_bot_pr` would not apply. The GitHub identity is undecided and now blocks.
  - seeds: `c43`
- `s19` — `challenge pass / data-flow lens: culture_rules/extensions/github/hooks.py:84-96, 450-459`: The webhook receiver maps only created and submitted actions; an edited issue comment is answered 'ignored'. Qodo's docs say a push re-review updates the existing comment, so later reviews raise no event. One review also raises several events (comment, inline comments, review).
  - seeds: `c44`
- `s20` — `challenge pass / hidden-dependencies lens: http.call body cap (culture_rules/extensions/http/port.py, DEFAULT_MAX_BODY 64 KiB)`: A PR's full findings with agent prompts and suggestions can exceed 64 KiB, and http.call truncates silently into a 'truncated' flag.
  - seeds: `c45`
- `s21` — `challenge pass / concurrency and operations lens: the merged-PR flow in the spec`: The spec's signal covers 1 merged PR. Nothing bounded the rate: every merge would start an agent run and could open another PR, and two runs could share a checkout.
  - seeds: `c46`
- `s22` — `challenge pass / adjacent-systems lens: qodo-cli issue 18 (devex-cli integration)`: devex-cli calls qodo.cli.main with --json and parses stdout. Moving logic into qodo.api can break it if output shapes shift.
  - seeds: `c47`
- `s23` — `challenge pass / adjacent-systems lens: qodo-cli pyproject.toml:22-23 and Qodo's CLI docs`: Both programs install a binary named qodo. 'which -a qodo' on this machine finds none installed, so the clash is not live here.
  - seeds: `q16` (question, resolved)
- `s24` — `challenge pass / observability and security lens: culture_rules/auth (two listeners, roles) and Qodo's key expiry (docs.qodo.ai manage-api-keys)`: The spec named the auth parts to cite but set no rule that routes need them, and nothing would show an expired personal key, which stops both flows silently.
  - seeds: `c48`, `c49`
- `s25` — `challenge pass / rollback and migration lens: docs/operations/rollout.md:20-24 (bundle shape, release.json)`: A bundle's optional release.json must name the culture-rules release, which would tie a bundle kept in qodo-cli to one culture-rules version; without it nothing states compatibility.
  - seeds: `c50`
- `s26` — `challenge pass / unexamined surfaces`: Not examined: the associate repo beyond its README and run help (contract, gate, CLAUDE.md); Qodo's live MCP server (no call made); culture-rules http.call and rollout code (read by a survey agent, not re-read in this pass); the qodo-cli web UI and server, which do not exist yet; lobes gateway routing between core and spark.
- `s27` — `challenge pass / depth`: Rigorous, on the security-sensitive signal: a service holding a personal API key and GitHub credentials, and an agent with write access fed untrusted text. No data-loss, migration or hardware signal applies.
- `s28` — `challenge pass / reversibility lens: CHANGELOG.md:131 and culture_rules/extensions/github (client.py:340-368, port.py)`: The engine's GitHub client has `get_pull`, `merge_pull`, `delete_ref` and `branch_tip` only; a push kind existed and was removed in 0.43.0. Opening a PR as the App is new client and port code inside the github extension, and it changes the pinned github-app actor when listed in params.actions.
  - seeds: `c53`, `c51`

## Decisions

- The agent that improves the Qodo configuration is associate (the Pi harness) on its cortex model, run headless with 'associate run' against a checkout. It changes files in the checkout and never pushes; the bridge opens the PR. It operates qodo-cli through the CLI.
- qodo-cli is the surface, the actor a workflow operates: directly through its API, through an agent that uses its MCP server or CLI, or through code that calls the API or CLI.
- On an opened PR, 'run Qodo' means the bridge collects and triages Qodo's findings once Qodo's own review has landed.
- The web UI ships inside the qodo-cli wheel. culture-rules does not present it. An npm package of the UI is tracked as a follow-up issue.
- The web UI's first version has three screens: findings per PR, configuration, and rules search.
- The culture-rules configuration for Qodo (actors, rules, workflows) is a bundle committed in qodo-cli, shipped disabled and imported with rollout import --bundle.
- associate is approved as the improvement agent, and it gets a bridge like qodo-cli does: the full set of surfaces, CLI, MCP server, HTTP API and web UI, built on the same bridge framework, which a culture-rules workflow reaches through configuration.
- The loop guard does not depend on Qodo's `ignore_bot_pr`. The mark the bridge sets (c36) is the only guard, because the PR's author depends on which GitHub identity the bridge uses.
- Configuration PRs are opened by culture-rules as its existing GitHub App, through a new generic action that opens a PR. The qodo bridge prepares and validates the change and does not open the PR itself. This replaces the earlier decision that the bridge opens it.
- qodo-cli's command is renamed from qodo to qodo-cli, because Qodo's own CLI is also named qodo.
- The design canvas at <https://claude.ai/artifact/JQLGKoX2zDwQKnSUQxFyWB> is part of this spec: it is the visual reference for the bridge UI's three screens (Findings, Configuration, Rules), in the AgentCulture tokens that culture-rules' web/src/culture-design carries. Its repos, PRs, findings and rules are sample content.

## Hard questions

- What does 'embedded' mean for the web surface? No app embeds another today, and culture-library, pocketsync and irc-lens send frame-ancestors 'none'. Candidates: the qodo UI framed inside the culture-rules editor, a UI mountable under another app's path, or a UI that ships inside the wheel like `culture_rules`/`web_dist`. (resolved: The UI ships in the wheel. An npm package is a follow-up issue on qodo-cli. culture-rules does not present the UI.)
- Qodo's own CLI is also installed as 'qodo' (docs.qodo.ai/agentic-toolbox/cli, 'qodo login'), and qodo-cli's entry point is 'qodo' (pyproject.toml:23). A service machine or agent with both has one name for two programs. Keep the name, add a second entry point, or rename? (resolved: Rename the entry point to qodo-cli.)
- What does 'run Qodo' on an opened PR do? (a) make Qodo review it, e.g. the App posts /review; (b) wait for Qodo's own review, then have the bridge collect and triage its findings; (c) both. Qodo already reviews PRs automatically where its GitHub app is installed. (resolved: Confirmed: the bridge collects and triages Qodo's findings when Qodo's own review lands. Further things to do come from a review of Qodo's API.)
- How does an improved Qodo configuration land after a merge? The engine has no push or open-PR action. Candidates: the agent's bridge pushes a branch and opens a PR with its own credentials, the bridge app does it through gh, or a new generic engine action does it as the GitHub App. (resolved: The bridge opens the PR with the improved configuration, and that PR must not start a loop.)
- How does culture-rules reach the bridge: http.call on a service actor (no culture-rules code), a new extensions/qodo package with typed kinds and qodo.\* events, or a sixth app surface in `APP_SURFACES`? 'Actor or surface' maps to these three. (resolved: Configuration only. The bridge has an API, and culture-rules is configured to call it. Tasks that need an agent give the agent qodo-cli's MCP server or CLI.)
- Where does the Qodo configuration (actors, rules, workflows) live: live-only as with the Jira flow, or as a bundle directory in qodo-cli that 'rollout import --bundle DIR' reads, so culture-rules' repo never names Qodo? (resolved: A bundle directory committed in qodo-cli, shipped disabled, imported with culture-rules rollout import --bundle DIR.)
- Where does the bridge framework live so a second bridge (the devague service) can reuse it without a new repo: cited copies inside qodo-cli only, or a new reference in agentfront beside python-cli that both repos cite?
- risk: With two bridges (qodo-cli and associate) the framework is cited twice from the first day, so its parts must be free of Qodo-specific code from the start.
- Which harness is the cortex agent: Qwen Code on cortex through a bridge (the earlier qwen-fixer shape), colleague work with the cortex seat, or llm.complete on cortex-spark2 producing the new file text for a deterministic step to write? (resolved: Pi. Its latest version supports MCP, but the CLI is enough.)
- How does culture-rules run a Pi agent? cultureagent's README (lines 36-43) lists three bridges, qwen, codex and claude, and no Pi bridge. Candidates: a Pi bridge added to cultureagent (a third repo), or a registered runner command that runs Pi headless on core. (resolved: associate gets a bridge like qodo-cli does: a service surface on associate that a workflow reaches through configuration. Use of associate is approved.)
- risk: associate reaches its model through the local lobes gateway, and cortex is served from spark while culture-rules runs on core. The run needs a machine that has both the checkout and a route to cortex.
- risk: A registered runner command is synchronous: associate's default timeout is 900 s, and the installed 0.9.0 may lack the --model flag the README documents.
- Which GitHub identity opens the bridge's PRs: a person's gh login (what qodo-cli uses today), the culture-rules GitHub App through a new generic action, or a GitHub App of the bridge's own? It decides the PR's author, its permissions and whether Qodo reviews it. (resolved: culture-rules' GitHub App opens the PR, through a generic open-a-PR action in culture-rules.)
- Does qodo config still write what Qodo reads? Check `best_practices.md` and \[`pr_reviewer`\] `extra_instructions` against Qodo's current configuration reference before the bridge builds on them.
- risk: An associate run can take up to 900 s and http.call is one synchronous request per step, so the associate bridge needs a start-then-read-result shape, or the cultureagent bridge protocol culture-rules already runs asynchronously with callbacks.
- risk: The associate repo was read only at its README and 'run --help'. Its CLAUDE.md, contract and release rules have not been surveyed and must be before its bridge is planned.
- risk: The GitHub App needs contents write and pull-request write on the target repos; github.push was removed in 0.43.0 with the PR fixer (CHANGELOG.md:131), so this permission and code path return in a narrower form.
- risk: The actor's pinned digest (`culture_rules`/actors/trusted.py, `TRUSTED_ACTOR_DIGESTS` for github-app) covers params.actions, so listing the new action on the live App actor changes its digest.

## Open parks

- [unknown_nonblocking] Where the bridge runs and with which credentials: a unit on core beside the API, the Qodo API key as a grant, and GitHub access for a service that today relies on a person's gh login.
- [unknown_nonblocking] Whether the associate bridge is built in this plan or in its own spec after the qodo bridge proves the framework.
- [unknown_nonblocking] qodo-cli's publish workflow watches only pyproject.toml and qodo/\*\* and has no Node job, so a web UI change would neither build nor publish. Plan-side: CI and publish changes.
- [unknown_nonblocking] Whether Qodo's terms allow one person's API key to back a shared service, and what happens to the flows when that person's key is revoked.
- [follow_up] Publish the web UI as an npm package: agentculture/qodo-cli issue 23.

## Resolved vagueness

- [unknown_blocking] What the bridge's web UI shows (reviews per PR, findings, configuration history, rules search) is undefined; the request names the surface but no screen. — resolved: Not resolved by the operator yet; kept open
- [unknown_blocking] Whether Qodo obeys a /review command posted by a GitHub App bot is unverified; it decides if a culture-rules step can trigger a review at all. — resolved: Left out. The first version relies on Qodo's own automatic review and does not post /`agentic_review`.
- [unknown_blocking] What the bridge's web UI shows (reviews per PR, findings, configuration history, rules search) is undefined; the request names the surface but no screen. Re-parked: v2 was closed by the agent in error, not by an operator decision. — resolved: First version shows three screens: findings per PR (with mark implemented and dismiss), each repo's configuration with its validation and the bridge's configuration PRs, and rules search.
