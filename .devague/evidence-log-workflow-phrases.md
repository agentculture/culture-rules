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
