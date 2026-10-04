import { describe, expect, it } from "vitest";
import { ApiError } from "./client";
import { GENERIC_GUIDANCE, KNOWN_CODES, guidanceFor, guidanceForError } from "./guidance";

describe("guidance table", () => {
  it("has a plain message and at least one fix for every known code", () => {
    expect(KNOWN_CODES.length).toBeGreaterThan(40);
    for (const code of KNOWN_CODES) {
      const g = guidanceFor(code);
      expect(g, code).not.toBe(GENERIC_GUIDANCE);
      expect(g.message.length, code).toBeGreaterThan(10);
      expect(g.fixes.length, code).toBeGreaterThanOrEqual(1);
      expect(g.message, code).not.toContain("_");
    }
  });

  it("covers the planned and server vocabularies", () => {
    for (const code of [
      "invalid_inputs", "actor_unavailable", "destination_refused", "extra_missing",
      "trigger_type_required", "paused", "rule_not_found", "workflow_required", "invalid_rule",
      "invalid", "not_found", "conflict", "rule_referenced", "forbidden", "forbidden_role",
      "bad_identity", "bad_token", "secret_literal", "malformed", "input_missing", "unknown_port",
      "trigger_kind_unknown", "trigger_cron_required", "trigger_param_required",
      "trigger_param_invalid", "action_kind_unknown", "action_param_required", "action_param_type",
      "not_implemented", "replay_invalid", "not_json",
    ]) {
      expect(KNOWN_CODES, code).toContain(code);
    }
  });

  it("answers the generic guided message for an unknown code, never raw text", () => {
    const g = guidanceFor("zz_totally_new");
    expect(g).toBe(GENERIC_GUIDANCE);
    expect(g.fixes.length).toBeGreaterThanOrEqual(1);
    const fromError = guidanceForError(new ApiError(500, "zz_totally_new", "Traceback: boom at 0x1"));
    expect(fromError.message).toBe(GENERIC_GUIDANCE.message);
    expect(fromError.message).not.toContain("boom");
  });

  it("falls back to a nested errors[] code when the top-level one is generic", () => {
    expect(guidanceFor("invalid", ["input_missing"]).fixes[0].kind).toBe("add-input");
    expect(guidanceFor("zz_new", ["zz_other"])).toBe(GENERIC_GUIDANCE);
  });

  it("types wire-port fixes with a port", () => {
    const fixes = guidanceFor("unknown_port").fixes;
    expect(fixes.some((f) => f.kind === "wire-port")).toBe(true);
  });
});
