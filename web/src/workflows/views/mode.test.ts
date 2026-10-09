import { afterEach, describe, expect, it, vi } from "vitest";
import { DEFAULT_VIEW_MODE, VIEW_MODE_KEY, VIEW_MODES, readViewMode, writeViewMode } from "./mode";

afterEach(() => {
  vi.restoreAllMocks();
  try {
    window.localStorage.clear();
  } catch {
    /* nothing to clear */
  }
});

describe("the workflow view mode, kept per viewer in localStorage", () => {
  it("offers Simple, Detailed and Debug, and starts on Simple", () => {
    expect(VIEW_MODES).toEqual(["simple", "detailed", "debug"]);
    expect(DEFAULT_VIEW_MODE).toBe("simple");
    expect(readViewMode()).toBe("simple");
  });

  it("reads back the mode it wrote", () => {
    writeViewMode("debug");
    expect(window.localStorage.getItem(VIEW_MODE_KEY)).toBe("debug");
    expect(readViewMode()).toBe("debug");
    writeViewMode("detailed");
    expect(readViewMode()).toBe("detailed");
  });

  it("ignores a stored value that is not a mode", () => {
    window.localStorage.setItem(VIEW_MODE_KEY, "sideways");
    expect(readViewMode()).toBe("simple");
  });

  it("falls back to the default when storage throws on read", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(readViewMode()).toBe("simple");
  });

  it("swallows a storage that throws on write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    expect(() => writeViewMode("debug")).not.toThrow();
  });

  it("survives a window whose localStorage accessor itself throws", () => {
    vi.spyOn(window, "localStorage", "get").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    expect(readViewMode()).toBe("simple");
    expect(() => writeViewMode("debug")).not.toThrow();
  });
});
