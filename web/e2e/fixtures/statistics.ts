import type { Page } from "@playwright/test";
import {
  STAT_MACHINES,
  STAT_RULES,
  STAT_WORKFLOWS,
  statRuns,
  statStatuses,
} from "../../src/statistics/fixture";

/**
 * Statistics-tab routes for the mocked API: the four machines of the
 * 'Chosen — Statistics' board (orin offline), their runs, and the proposed
 * `GET /machines/status`. Compose after `mockApi(page)`: Playwright runs the
 * most recently registered matching route first, so these win.
 */
export async function mockStatistics(page: Page, opts: { status?: boolean } = {}) {
  const now = Date.now();
  const bodies: Record<string, unknown> = {
    "/api/machines": { items: STAT_MACHINES },
    "/api/rules": { items: STAT_RULES },
    "/api/workflows": { items: STAT_WORKFLOWS },
    "/api/runs": { items: statRuns(now) },
    ...(opts.status === false ? {} : { "/api/machines/status": { items: statStatuses(now) } }),
  };
  await page.route("**/api/**", async (route) => {
    const body = bodies[new URL(route.request().url()).pathname];
    if (body === undefined) return route.fallback();
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
}
