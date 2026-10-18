/**
 * Whether the workflows pane is collapsed to its rail, kept per viewer in this browser's
 * localStorage only. Storage can be missing, blocked or full: every access is wrapped, and the
 * pane works the same without it (expanded).
 */
export const PANE_KEY = "culture-rules.workflows-pane";

export function readPaneCollapsed(): boolean {
  try {
    return window.localStorage.getItem(PANE_KEY) === "collapsed";
  } catch {
    return false;
  }
}

export function writePaneCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(PANE_KEY, collapsed ? "collapsed" : "expanded");
  } catch {
    /* storage unavailable: the choice lasts this page only */
  }
}
