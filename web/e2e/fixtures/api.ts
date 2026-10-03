import type { Page } from "@playwright/test";
import {
  MACHINES,
  RULES,
  WHOAMI,
  WORKFLOWS,
  runsFor,
} from "../../src/fixtures/rules-fixture";

/**
 * Serve the culture-rules API (api/openapi.json shapes) by request
 * interception under the same-origin `/api` prefix the bundle calls. Every
 * call is recorded so a test can assert where identity came from.
 */
export async function mockApi(page: Page): Promise<string[]> {
  const calls: string[] = [];
  const now = Date.now();
  const bodies: Record<string, unknown> = {
    "/api/whoami": WHOAMI,
    "/api/rules": { items: RULES },
    "/api/machines": { items: MACHINES },
    "/api/workflows": { items: WORKFLOWS },
    "/api/runs": { items: runsFor(now) },
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    calls.push(`${route.request().method()} ${url.pathname}`);
    const body = bodies[url.pathname];
    if (body === undefined) {
      await route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ error: { code: "not_found", message: url.pathname, errors: [] } }),
      });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
  });
  return calls;
}
