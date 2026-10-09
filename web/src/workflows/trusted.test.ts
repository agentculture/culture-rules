import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { isTrustedWorkflow, TRUSTED_WORKFLOW_ROLES } from "./trusted";

const TRUSTED_PY = resolve(dirname(fileURLToPath(import.meta.url)), "../../../culture_rules/actors/trusted.py");

describe("trusted workflows (c32, d6)", () => {
  it("mirrors every ROLE_* in culture_rules/actors/trusted.py, and nothing else", () => {
    const roles = [...readFileSync(TRUSTED_PY, "utf8").matchAll(/^ROLE_\w+ = "([^"]+)"$/gm)].map((m) => m[1]);
    expect(roles.length).toBeGreaterThan(0);
    expect([...TRUSTED_WORKFLOW_ROLES].sort()).toEqual(roles.sort());
  });

  it("names the trusted ids only", () => {
    expect(isTrustedWorkflow("pr-fix")).toBe(true);
    expect(isTrustedWorkflow("report-secrets")).toBe(false);
    expect(isTrustedWorkflow(null)).toBe(false);
  });
});
