/**
 * Canvas zoom (d19): the bounds, the step and the fit, as pure functions.
 *
 * The canvas is sized like a document: at zoom z its height and the graph's
 * width scale by z, so zooming out shrinks the board (a wide graph then fits
 * without sideways scrolling) and zooming in grows it, while a plain wheel
 * keeps scrolling the page. Zoom 1 is the board's 190px cards.
 */

export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 2;
/** One zoom-in step multiplies by this; one zoom-out step divides. */
export const ZOOM_STEP = 1.25;
/** A button or key zoom animates this long, unless the reader prefers reduced motion. */
export const ZOOM_DURATION_MS = 200;

/** `z` within [MIN_ZOOM, MAX_ZOOM], to two decimals (so steps land on round values). */
export function clampZoom(z: number): number {
  if (!Number.isFinite(z)) return 1;
  return Math.round(Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z)) * 100) / 100;
}

/** One step in (`+1`) or out (`-1`) from `z`. */
export function stepZoom(z: number, direction: 1 | -1): number {
  return clampZoom(direction > 0 ? z * ZOOM_STEP : z / ZOOM_STEP);
}

/**
 * Fit: the zoom at which a graph `graphWidth` wide (at zoom 1) fits a canvas
 * `width` wide with `margin` on each side — never above 1, so fitting a graph
 * that already fits is also the reset to the board's size.
 */
export function fitZoom(graphWidth: number, width: number, margin: number): number {
  if (graphWidth <= 0 || width <= 0) return 1;
  return clampZoom(Math.min(1, (width - 2 * margin) / graphWidth));
}

/** `125%` — the readout beside the zoom buttons. */
export function zoomPercent(z: number): string {
  return `${Math.round(z * 100)}%`;
}

/** Whether the reader asked for reduced motion (no animated zoom then). */
export function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return true;
  // Unknown (a stub that answers nothing) counts as reduced: never animate by mistake.
  return window.matchMedia("(prefers-reduced-motion: reduce)")?.matches ?? true;
}

/** The zoom key a keydown asks for: `+`/`=` in, `-` out, `0` fit; null for any other key. */
export function zoomKey(e: Pick<KeyboardEvent, "key" | "ctrlKey" | "metaKey" | "altKey">): "in" | "out" | "fit" | null {
  if (e.ctrlKey || e.metaKey || e.altKey) return null; // the browser's own page zoom
  if (e.key === "+" || e.key === "=") return "in";
  if (e.key === "-" || e.key === "_") return "out";
  if (e.key === "0") return "fit";
  return null;
}
