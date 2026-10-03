import { describe, expect, it } from "vitest";
import { slugFor, trimDashes } from "./rules-view";

describe("slugFor", () => {
  it("lower-cases and dashes a name", () => {
    expect(slugFor("Disk is nearly full", [])).toBe("disk-is-nearly-full");
  });
  it("drops leading and trailing separators", () => {
    expect(slugFor("  -- Deploy! --  ", [])).toBe("deploy");
  });
  it("falls back to `rule` when nothing is left", () => {
    expect(slugFor("!!!", [])).toBe("rule");
  });
  it("falls back to the given word when nothing is left", () => {
    expect(slugFor("!!!", [], "workflow")).toBe("workflow");
  });
  it("is unique among taken ids", () => {
    expect(slugFor("Deploy", ["deploy", "deploy-2"])).toBe("deploy-3");
  });
  it("stays fast on a long adversarial name", () => {
    const start = performance.now();
    expect(slugFor(`a${"-!".repeat(50_000)}a`, [])).toBe("a-a");
    expect(performance.now() - start).toBeLessThan(250);
  });
});

describe("trimDashes", () => {
  it("drops dashes at both ends only", () => {
    expect(trimDashes("--a-b--")).toBe("a-b");
    expect(trimDashes("a")).toBe("a");
    expect(trimDashes("---")).toBe("");
    expect(trimDashes("")).toBe("");
  });
  it("is linear on a long inner run of dashes", () => {
    const start = performance.now();
    expect(trimDashes(`a${"-".repeat(100_000)}a`)).toBe(`a${"-".repeat(100_000)}a`);
    expect(performance.now() - start).toBeLessThan(250);
  });
});
