# 0.22.0 rollout + newspaper move (2026-10-22, ~21:23-21:40 IDT)

Release CLI: rollout-0220 worktree at b3a3025, wheel culture_rules-0.22.0-py3-none-any.whl.

- plan: API 0.21.0; snapshot check-thor-uptime, daily-newspaper; no new rules/variables
- idle: 0 non-terminal runs; pause --apply: engine paused
- upgrade spark (shared venv) / thor / orin / spark2 (install.sh): each "0.22.0 installed", node unit active
- migrate-notes dry run: 0 workflows (no apply)
- seed --apply: 0 set, 12 skipped; import --apply: 32 unchanged; migrate-orphans dry run: 0
- enable --apply: 0 enabled, 2 already enabled, 25 left disabled
- resume --apply: engine resumed
- verify: nodes 4 on 0.22.0; api 0.22.0; engine running; rules 2 = snapshot; no_builtin none -> verified

Newspaper move (cortex-spark2 only; operator: spark is testing models):

1. variables set model_gateways ["spark=http://127.0.0.1:8001"] --apply
2. enrol dry run before disable: gateway lists 13 models, only cortex-* is cortex-spark2; refused model_taken: newspaper-llm
3. actors disable newspaper-llm --force --apply -> enabled false (in_use was daily-newspaper summarise.body[summary])
4. enrol-models --gateway spark --match cortex-spark2 --pool cortex-spark2=qwen-spark2 --apply -> create cortex-spark2
   (machine spark, base_url <http://127.0.0.1:8001>, model cortex-spark2, pool qwen-spark2, max_concurrency 1, grant:LOBES_GATEWAY_API_KEY)
5. workflows repoint-actor daily-newspaper --from newspaper-llm --to cortex-spark2 --apply -> 1 place, revision 2
   warning: step summarise notes mention 'newspaper-llm' (notes unchanged)
6. actors references newspaper-llm: none; cortex-spark2: daily-newspaper summarise.body[summary]

Pending at the time: the 2026-10-23 05:00 issue.
7. workflows update daily-newspaper (expected-revision 2) --apply: summarise notes now "llm.complete on cortex-spark2 (pool qwen-spark2)" (the committed bundle's wording) -> revision 3; no 'newspaper-llm' left in the workflow; references unchanged; enabled rules check-thor-uptime, daily-newspaper

## Morning checklist (2026-10-23)

- 05:00 Asia/Jerusalem = 02:00 UTC daily-newspaper run: `cr runs list` (rule daily-newspaper), run status, summarise iterations 4/4 succeeded on cortex-spark2 (none skipped / 'Summary unavailable.')
- the email went out (email.send step ok) and library.push ok
- then: devague evidence for the newspaper move, /validate-delivery, /summarize-delivery; operator adjudicates lapse l2 (t9's MCP tools never called over stdio)

## Result (2026-10-23)

- run-e011e3fc953e952e51f7f3607a1fe9c3: succeeded 02:01:09-02:03:34Z; summarise[0..3]/summary succeeded on cortex-spark2 (served unsloth/Qwen3.8-27B-NVFP4); 0 skipped steps; no 'Summary unavailable.' in any output; email sent (message 01a1667f…); push succeeded
- operator: "the morning newsletter is up"
- live actors (0.23.0): endpoint model cortex-spark2 = cortex-spark2 (enabled), newspaper-llm (disabled); no other shared model
