import { describe, expect, it } from "vitest";
import type { Port, WorkflowDef } from "../api/workflows";
import { CANVAS_TOP, canvasBounds, canvasHeight, cardHeight, compactHeight, nodeHeights } from "./layout";

const ports = (n: number, prefix: string): Port[] =>
  Array.from({ length: n }, (_, i) => ({ name: `${prefix}${i + 1}`, type: "string" }));

/** A step with 8 ports (4 in, 4 out), stacked below another step. */
const TALL: WorkflowDef = {
  id: "tall",
  name: "Tall",
  inputs: [{ name: "a", type: "string" }],
  steps: [
    { id: "first", kind: "code", inputs: ports(1, "i"), outputs: ports(1, "o") },
    { id: "eight", kind: "code", inputs: ports(4, "i"), outputs: ports(4, "o") },
  ],
  outputs: [],
};

const POSITIONS = {
  inputs: { x: 0, y: 0 },
  first: { x: 250, y: 0 },
  eight: { x: 250, y: 300 },
  outputs: { x: 500, y: 0 },
};

describe("canvas bounds", () => {
  it("measures each card compact, and the expanded card from its port count", () => {
    const heights = nodeHeights(TALL);
    expect(heights.eight).toBe(compactHeight());
    expect(heights.inputs).toBe(compactHeight(true));
    expect(compactHeight()).toBeLessThan(cardHeight(2));
    const expanded = nodeHeights(TALL, {}, "eight");
    expect(expanded.eight).toBe(cardHeight(8));
    expect(expanded.first).toBe(compactHeight());
    expect(nodeHeights(TALL, {}, "inputs").inputs).toBe(cardHeight(1, true));
  });

  it("a step with 8 ports, expanded, fits inside the canvas", () => {
    const bounds = canvasBounds(POSITIONS, nodeHeights(TALL, {}, "eight"));
    const bottom = POSITIONS.eight.y + cardHeight(8);
    expect(bounds.maxY).toBeGreaterThanOrEqual(bottom);
    // In canvas pixels: the card's bottom edge sits above the canvas's bottom (and the + below it).
    const height = canvasHeight(bounds);
    expect(CANVAS_TOP + bottom - bounds.minY).toBeLessThanOrEqual(height - 72);
  });

  it("a measured card taller than its estimate wins", () => {
    const bounds = canvasBounds(POSITIONS, nodeHeights(TALL, { eight: 900 }));
    expect(bounds.maxY).toBe(300 + 900);
  });

  it("an empty graph still has the board's minimum height", () => {
    expect(canvasHeight(canvasBounds({}, {}))).toBe(570);
  });
});
