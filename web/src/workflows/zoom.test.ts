import { afterEach, describe, expect, it, vi } from "vitest";
import { canvasHeight, CANVAS_MIN_HEIGHT, CANVAS_TOP } from "./layout";
import {
  MAX_ZOOM,
  MIN_ZOOM,
  clampZoom,
  fitZoom,
  prefersReducedMotion,
  stepZoom,
  zoomKey,
  zoomPercent,
} from "./zoom";

describe("canvas zoom (d19)", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("clamps to 25%–200% and rounds to two decimals", () => {
    expect(clampZoom(0.1)).toBe(MIN_ZOOM);
    expect(clampZoom(9)).toBe(MAX_ZOOM);
    expect(clampZoom(1.23456)).toBe(1.23);
    expect(clampZoom(Number.NaN)).toBe(1);
  });

  it("steps by 1.25 and stops at the bounds", () => {
    expect(stepZoom(1, 1)).toBe(1.25);
    expect(stepZoom(1, -1)).toBe(0.8);
    expect(stepZoom(MAX_ZOOM, 1)).toBe(MAX_ZOOM);
    expect(stepZoom(MIN_ZOOM, -1)).toBe(MIN_ZOOM);
  });

  it("fits a wide graph to the width, never above 1 (fit is also the reset)", () => {
    expect(fitZoom(1960, 1000, 20)).toBe(0.48); // 0.4898 rounds down, never past the width
    expect(1960 * fitZoom(1960, 1000, 20) + 40).toBeLessThanOrEqual(1000);
    for (const width of [600, 777, 990, 1000, 1234]) {
      expect(1960 * fitZoom(1960, width, 20) + 40).toBeLessThanOrEqual(width);
    }
    expect(fitZoom(400, 1000, 20)).toBe(1);
    expect(fitZoom(100_000, 1000, 20)).toBe(MIN_ZOOM);
    expect(fitZoom(0, 1000, 20)).toBe(1);
    expect(fitZoom(400, 0, 20)).toBe(1);
  });

  it("reads out as a percentage", () => {
    expect(zoomPercent(1)).toBe("100%");
    expect(zoomPercent(0.8)).toBe("80%");
  });

  it("maps + = - _ 0 to in / out / fit, and leaves the browser's ctrl/cmd zoom alone", () => {
    const key = (k: string, mods: Partial<KeyboardEvent> = {}) =>
      zoomKey({ key: k, ctrlKey: false, metaKey: false, altKey: false, ...mods });
    expect(key("+")).toBe("in");
    expect(key("=")).toBe("in");
    expect(key("-")).toBe("out");
    expect(key("_")).toBe("out");
    expect(key("0")).toBe("fit");
    expect(key("a")).toBeNull();
    expect(key("+", { ctrlKey: true })).toBeNull();
    expect(key("0", { metaKey: true })).toBeNull();
  });

  it("follows prefers-reduced-motion, and treats no matchMedia as reduced", () => {
    vi.stubGlobal("matchMedia", (q: string) => ({ matches: q.includes("reduce") }));
    expect(prefersReducedMotion()).toBe(true);
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    expect(prefersReducedMotion()).toBe(false);
    vi.stubGlobal("matchMedia", undefined);
    expect(prefersReducedMotion()).toBe(true);
  });

  it("scales the canvas height with the zoom, never under the board's", () => {
    const bounds = { minX: 0, maxX: 800, minY: 0, maxY: 1000 };
    expect(canvasHeight(bounds, 1) - canvasHeight(bounds, 0.5)).toBe(500);
    expect(canvasHeight(bounds, 2)).toBe(canvasHeight(bounds) + 1000);
    expect(canvasHeight({ ...bounds, maxY: 10 }, 0.25)).toBe(CANVAS_MIN_HEIGHT);
    expect(canvasHeight(bounds)).toBeGreaterThan(CANVAS_TOP + 1000);
  });
});
