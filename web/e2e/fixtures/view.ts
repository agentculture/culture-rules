import type { Page } from "@playwright/test";
import { VIEW_MODE_KEY, type ViewMode } from "../../src/workflows/views/mode";

/**
 * Open every workflow in `mode` (the view switch's choice, kept per viewer in
 * localStorage): the canvas scenarios written before the fold drive the
 * Detailed (steps) view, while a workflow now opens in Simple by default.
 * Set before any page script runs, on every navigation of `page`.
 */
export async function withView(page: Page, mode: ViewMode = "detailed"): Promise<void> {
  await page.addInitScript(
    ([key, value]) => {
      try {
        window.localStorage.setItem(key, value);
      } catch {
        // no storage: the page opens in Simple, and the scenario says so by failing
      }
    },
    [VIEW_MODE_KEY, mode] as const,
  );
}
