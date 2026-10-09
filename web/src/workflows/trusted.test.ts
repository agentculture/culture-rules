import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { isTrustedWorkflow, TRUSTED_WORKFLOW_ROLES } from "./trusted";

const TRUSTED_PY = resolve(dirname(fileURLToPath(import.meta.url)), "../../../culture_rules/actors/trusted.py");

describe("trusted workflows (c32, d6)", () => {
  it("mirrors every ROLE_* in culture_rules/actors/trusted.py, and nothing else", () => {
    const text = readFileSync(TRUSTED_PY, "utf8").replaceAll("\r\n", "\n");
    // Every module-level ROLE_* line must parse: a role written in a shape the pattern misses
    // fails here instead of dropping out of both lists unnoticed.
    const declared = text.match(/^ROLE_\w+\b/gm) ?? [];
    const roles = [...text.matchAll(/^ROLE_\w+\s*(?::[^=\n]+)?=\s*["']([^"'\n]+)["']/gm)].map((m) => m[1]);
    expect(roles.length).toBeGreaterThan(0);
    expect(roles).toHaveLength(declared.length);
    expect([...TRUSTED_WORKFLOW_ROLES].sort()).toEqual(roles.sort());
  });

  it("names the trusted ids only", () => {
    expect(isTrustedWorkflow("pr-fix")).toBe(true);
    expect(isTrustedWorkflow("report-secrets")).toBe(false);
    expect(isTrustedWorkflow(null)).toBe(false);
  });
});
