import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { cleanup, configure } from "@testing-library/react";

// Under a busy full-suite run (every file in parallel, React Flow measuring and
// elkjs laying out in each), a canvas can take longer than Testing Library's
// default 1s to reach the accessibility tree, and a whole scenario longer than
// vitest's default 5s. Those were the WorkflowsZoomDescribe "flakes" (they
// passed alone): the waits were right, their budgets were not. Generous
// budgets cost nothing on a pass; only a real failure waits them out.
configure({ asyncUtilTimeout: 5_000 });
vi.setConfig({ testTimeout: 20_000 });

// jsdom ships neither ResizeObserver nor DOMMatrix, both of which React Flow
// touches on mount. Component tests here render the presentational layer
// rather than a live canvas, but the polyfill keeps any incidental import
// from exploding.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
    ResizeObserverStub;
}

// jsdom's Blob has no text() (every browser the build targets does, so the
// app calls it directly: src/workflows/IoControls.tsx readText). Test-only
// polyfill over jsdom's FileReader.
if (typeof Blob.prototype.text !== "function") {
  Object.defineProperty(Blob.prototype, "text", {
    configurable: true,
    writable: true,
    value(this: Blob): Promise<string> {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result ?? ""));
        reader.onerror = () => reject(reader.error ?? new Error("could not read blob"));
        reader.readAsText(this);
      });
    },
  });
}

// matchMedia is the reduced-motion signal's only source. Default every query
// to "no preference"; individual tests override it.
if (!window.matchMedia) {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));
}

afterEach(() => {
  cleanup();
});
