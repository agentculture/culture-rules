# workflow-connecting-phrases evidence log (input for /validate-delivery)

## 2026-10-17 — t1 before-state on main b146956 (c19, h16, c20, h17; wording per d1)

Read with `git show b146956:<path>`; read-only, no code changes.

- `docs/rules/pr-fixer/workflows/pr-fix.json:2`: the stored description starts
  `"d21, d30: quiet period -> GitGuardian hold (d25: no fix while it fails`.
  The same text prints from `culture-rules workflows describe pr-fix`'s source
  definition.
- `web/src/workflows/simple/SimpleView.tsx:2-10`: "The Simple view (canvas
  Fold-Editor, spec c9): a workflow as When / Then", with When entry cards and
  a Then column. Lines 76-94 render the steps column (`fold-col--steps`, an
  `ol.fold-steps` of Start, each step with where it runs, End). This is the
  fold layout, not a React Flow canvas (d1).
- `web/src/routes/Workflows.tsx:55` imports `WorkflowList`, and `:1377`
  renders it beside the open workflow. The tab has no overview state that hides
  the list.
- No Workflow or Step field other than `description` carries prose
  (`culture_rules/model/workflow.py` at b146956: Port, Variable, Output, Edge,
  Step and Workflow; only `description` is free text on Step and Workflow).

## Closeout: announcement elements

t19 criterion 2. Each element of the announcement maps to the task criterion
that proves it and the tests that hold it (names grepped from the tree on
`rules/wp-t19`, 2026-10-18).

| Element | Proven by |
|---------|-----------|
| Description (plain language) and notes (technical) on Workflow and Step | t2 c1–c3: `tests/model/test_migrate_notes.py::test_committed_pr_fixer_workflows_keep_their_role`, `tests/server/test_openapi_contract.py::test_served_openapi_equals_committed_file` (d3: schemas carry notes); t3 c1–c2: `tests/test_boundaries.py::test_engine_node_actors_never_read_workflow_or_step_text`, `::test_no_summarize_verb_or_tool_and_no_summary_by_field` |
| Paragraph (`GET /workflows/{id}/describe`, `workflows describe`) | t6 c1–c4, t8 c1–c2: `tests/server/test_describe_api.py::test_workflow_describe_has_description_and_paragraph`, `::test_workflow_describe_is_viewer_readable_and_matches_the_library`, `tests/cli/test_describe_verbs.py::test_mcp_workflows_describe_carries_description_and_paragraph`, `::test_describe_puts_each_stand_in_in_brackets`, `::test_describe_marks_a_disabled_rule`; t18 c3: `tests/model/test_describe_paragraphs_fixture.py::test_c9_no_committed_workflow_falls_back_to_a_generated_line` |
| Phrases (one table, no colons, one sentence per line; d5, d11, d13) | t6 c2–c4: `tests/model/test_paragraph.py::test_the_table_is_lead_ins_connectors_trailing_clauses_and_sentences`, `::test_compose_joins_parts_with_commas_and_no_colon`, `::test_sequence_joins_one_sentence`, `::test_three_siblings_read_a_b_and_c`, `::test_consecutive_steps_sharing_a_condition_join_one_sentence`, `::test_committed_paragraphs_use_only_table_phrases_and_verbatim_descriptions`, `::test_an_empty_description_stands_in_with_the_step_name`; t5: `tests/model/test_describe.py::test_condition_words_read_the_empty_string_as_empty_but_symbols_keep_it` |
| Rule sentences and the Then side | t7 c1–c2: `tests/model/test_rule_paragraph.py::test_rule_sentence_per_trigger_kind`, `::test_rule_sentence_relationships`, `::test_rule_sentence_never_raises`, `::test_then_continues_into_each_workflow_once_in_order`, `::test_then_status_comments_and_chain_ends` |
| Cards (the overview) | t13 c1, c3: `web/src/workflows/list/overview.test.tsx` "renders one card per workflow with name, description, step count and apps; no list pane", "counts top-level steps and loop bodies"; Playwright `web/e2e/workflows-described.spec.ts` "/workflows: one card per workflow with its description (or the gap) and no pane", `web/e2e/workflows-list.spec.ts` "the overview is one card per workflow across the full main width (screenshot)" |
| Apps touched | t13 c2: `web/src/workflows/list/overview.test.tsx` "takes app actors with a known surface from action steps, loop bodies, ai/actor_task steps and Then actions", "leaves out unknown ids, non-app actors and unknown surfaces", "reads the committed newspaper: email and search apps, not the agent or the service" |
| Notes migration (`workflows migrate-notes`, committed files) | t11 c1–c3: `tests/model/test_migrate_notes.py::test_moves_workflow_steps_and_loop_bodies_byte_for_byte`, `tests/server/test_migrate_routes.py::test_notes_apply_moves_audits_once_and_bumps_revision`, `::test_notes_second_apply_changes_nothing`, `::test_notes_migration_is_admin`, `tests/cli/test_migrate_verbs.py::test_workflows_migrate_notes_dry_run_then_apply`; t12 c1–c2: `tests/rules/test_committed_notes_migrated.py::test_committed_workflow_is_already_migrated`, `::test_every_description_is_empty`; t10: `tests/io/test_exchange.py::test_import_keeps_stored_descriptions_when_committed_empty`; t17: `tests/cli/test_rollout_verbs.py::test_plan_places_migrate_notes_after_upgrade_before_import`; live: t20 c1–c2 (operator, pending) |
| Versioned digest (v1 and v2) | t4 c1–c3 (d2, d12): `tests/rules/test_trusted_workflow.py::test_notes_do_not_change_the_v1_digest`, `::test_v2_ignores_description_and_notes_at_every_level`, `::test_v2_changes_on_any_other_field`, `::test_moving_description_into_notes_keeps_v2_and_the_role`, `::test_a_run_matches_by_v1_or_v2_and_unknown_under_both_is_refused`, `::test_v1_and_v2_matching_different_roles_fails_closed`, `::test_every_v2_source_is_pinned_in_its_role` |
| Expected version (the server-owned `revision`, d7) | t9 c1–c2: `tests/server/test_workflow_revision.py::test_stale_revision_is_409_naming_the_current_one_and_writes_nothing`, `::test_missing_expected_revision_is_409_and_writes_nothing`, `::test_two_clients_read_the_same_revision_first_wins_second_gets_409`, `::test_import_never_takes_the_revision_from_the_bundle`, `::test_trust_digests_ignore_the_revision`, `tests/cli/test_workflow_revision_verbs.py::test_update_reads_the_revision_and_sends_it`, `::test_mcp_workflows_update_sends_the_revision`; t16 c1: `web/src/routes/WorkflowsRevision.test.tsx` "two tabs on revision 3: the first saves, the second gets the conflict, keeps its draft, Reload replaces it" |
| d6: compact pane that collapses to a rail (t21) | `web/src/workflows/list/pane.test.tsx` "lists names only, one short row each, the open one marked current", "collapses to only the toggle and expands back, by keyboard, with aria-expanded"; `web/e2e/workflows-list.spec.ts` "an open workflow shows a compact pane left of the board: names only, the open one current (screenshot)" |
| d13: no overview section, `rules migrate-orphans`, muted "disabled" tag, notes on top of Detailed, no legend in Simple | `tests/server/test_migrate_routes.py::test_orphans_apply_makes_d7_workflows_audited_and_is_idempotent`, `::test_orphans_is_admin_only`, `tests/cli/test_migrate_verbs.py::test_migrate_orphans_dry_run_then_apply`, `tests/cli/test_rollout_verbs.py::test_plan_places_migrate_notes_after_upgrade_before_import` (orphans after import); Playwright `web/e2e/workflows-described.spec.ts` "d13: disabled rules read muted with a tag, not struck through; Simple has no legend", "pr-fix: Simple shows the description and the joined paragraph, Detailed the notes" |
| d14: in Simple a step opens its panel, a rule sentence opens its editor | `web/src/workflows/simple/SimpleView.test.tsx` "a step's text opens its panel (the page mounts the Detailed one); nothing is edited inline (d14)", "a rule's sentence carries its rule id and opens the rule's editor"; Playwright `web/e2e/workflows-described.spec.ts` "d14: a step opens its panel, a rule opens its editor with the enable switch; the selected words are highlighted" |
| Simple text equals the API paragraph (o4) | t14 c1–c2, t18 c1–c2: `web/src/workflows/simple/SimpleView.test.tsx` "the visible text equals the API's paragraph.text", "no joining phrase from the API's table is written in the Simple sources"; Playwright `web/e2e/workflows-described.spec.ts` "${id}: Simple text equals the API paragraph; every step in order; no invented phrase" |
| Expected version 0.21.0 and docs in step | t19 c1–c2: `pyproject.toml`, `CHANGELOG.md` `[0.21.0]`, `docs/rules/pr-fixer/release.json` (`tests/packaging/test_deploy_data.py::test_release_json_matches_pyproject_and_names_only_what_ships`), the four prompt files, `scripts/harness-smoke.py --stage config` |
