import { describe, expect, it } from "vitest";
import { CHART_PALETTE, machineColors, NEUTRAL_MACHINE } from "./chart";

describe("chart palette", () => {
  it("pins the light set to the design canvas's machine colors", () => {
    expect(CHART_PALETTE.light).toEqual(["#0a8a78", "#b4531f", "#3b4fb0"]);
  });

  it("carries a dark set of the same length", () => {
    expect(CHART_PALETTE.dark).toHaveLength(CHART_PALETTE.light.length);
  });

  it("gives machines slots in their listed order and cycles past the end", () => {
    const colors = machineColors(["spark", "thor", "spark2", "orin"]);
    expect(colors.get("spark")).toBe(0);
    expect(colors.get("thor")).toBe(1);
    expect(colors.get("spark2")).toBe(2);
    expect(colors.get("orin")).toBe(0);
  });

  it("has a neutral for unplaced or unknown machines", () => {
    expect(NEUTRAL_MACHINE).toMatch(/^#[0-9a-f]{6}$/);
  });
});
