/**
 * The workflow canvas's view mode — Simple, Detailed or Debug — kept per
 * viewer in this browser's localStorage only (spec h6). Storage can be
 * missing, blocked or full (a private window, blocked site data): every
 * access is wrapped, and the editor works the same without it.
 */
export type ViewMode = "simple" | "detailed" | "debug";

export const VIEW_MODES: readonly ViewMode[] = ["simple", "detailed", "debug"];

export const VIEW_MODE_LABELS: Record<ViewMode, string> = {
  simple: "Simple",
  detailed: "Detailed",
  debug: "Debug",
};

export const DEFAULT_VIEW_MODE: ViewMode = "simple";

export const VIEW_MODE_KEY = "culture-rules.workflow-view";

const isViewMode = (value: unknown): value is ViewMode =>
  typeof value === "string" && (VIEW_MODES as readonly string[]).includes(value);

/** The mode this viewer last chose, or Simple. Never throws. */
export function readViewMode(): ViewMode {
  try {
    const stored = window.localStorage.getItem(VIEW_MODE_KEY);
    return isViewMode(stored) ? stored : DEFAULT_VIEW_MODE;
  } catch {
    return DEFAULT_VIEW_MODE;
  }
}

/** Remember the chosen mode for this viewer; a storage failure only means it is not kept. */
export function writeViewMode(mode: ViewMode): void {
  try {
    window.localStorage.setItem(VIEW_MODE_KEY, mode);
  } catch {
    // Not kept: the mode still applies until the page is left.
  }
}
