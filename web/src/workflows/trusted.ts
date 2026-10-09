// TEMPORARY (PR #32 diagnostic): touch a web file so Sonar runs its JS security sensor; removed before merge.
/**
 * The PR fixer's trusted workflows (culture_rules/actors/trusted.py). The engine trusts by
 * definition digest only; the digest covers the workflow's id, and every trusted digest's id is
 * its role's name (tests/rules/test_trusted_role_ids.py pins that). So only a workflow with one
 * of these ids can be trusted, and a saved change to it gives it a new, untrusted digest: its
 * runs still start, but the chain will not review or push their work until the new digest ships
 * to every node (spec c32, deviation d6). The editor asks before such a save; a stored copy that
 * was already edited is asked about too (over-warning, never missing one).
 *
 * Mirrored here by role name so the editor can warn before a save; trusted.test.ts keeps this
 * list in step with the ROLE_* constants in trusted.py.
 */
export const TRUSTED_WORKFLOW_ROLES: readonly string[] = ["pr-fixer", "pr-fix", "review-commit", "publish-fix"];

export function isTrustedWorkflow(id: string | null | undefined): boolean {
  return typeof id === "string" && TRUSTED_WORKFLOW_ROLES.includes(id);
}
