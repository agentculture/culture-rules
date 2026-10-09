/**
 * The workflows the engine trusts to push (culture_rules/actors/trusted.py): trust is a set of
 * definition digests per role, and each role is named after the stored workflow it pins. Any
 * saved change to such a workflow changes its digest, so its runs stop being trusted (they still
 * run, they can no longer push) until the new digest ships in a release (spec c32, deviation d6).
 *
 * Mirrored here by role name so the editor can warn before a save; trusted.test.ts keeps this
 * list in step with the ROLE_* constants in trusted.py.
 */
export const TRUSTED_WORKFLOW_ROLES: readonly string[] = ["pr-fixer", "pr-fix", "review-commit", "publish-fix"];

export function isTrustedWorkflow(id: string | null | undefined): boolean {
  return typeof id === "string" && TRUSTED_WORKFLOW_ROLES.includes(id);
}
