import { afterAll, beforeAll } from "vitest";

/**
 * jsdom lays nothing out, and React Flow keeps an unmeasured node
 * `visibility: hidden` (which also blanks its accessible name). Give every
 * element a size and a ResizeObserver that reports it, as React Flow's own
 * testing guide does, so the canvas renders as it would in a browser.
 */
export class MeasuringResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    const size = { width: 190, height: 120 };
    const box = [{ inlineSize: 190, blockSize: 120 }];
    const entry = { target, contentRect: size, borderBoxSize: box, contentBoxSize: box };
    this.callback([entry as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

export class DOMMatrixStub {
  m22 = 1;
}

/** Give every HTMLElement a 190x120 offset size for the file's tests (restored after). */
export function useMeasuredLayout(): void {
  const sized = ["offsetWidth", "offsetHeight"] as const;
  const original = sized.map((k) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, k));
  beforeAll(() => {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", { configurable: true, get: () => 190 });
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", { configurable: true, get: () => 120 });
  });
  afterAll(() => {
    sized.forEach((k, i) => {
      if (original[i]) Object.defineProperty(HTMLElement.prototype, k, original[i]!);
    });
  });
}
