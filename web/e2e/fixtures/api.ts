import type { Page } from "@playwright/test";
import {
  ACTORS,
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
 * `roles` replaces the signed-in identity's roles (e.g. `["viewer", "editor"]`
 * for a non-admin).
 */
export async function mockApi(
  page: Page,
  options: { roles?: string[] } = {},
): Promise<string[]> {
  const calls: string[] = [];
  const now = Date.now();
  const bodies: Record<string, unknown> = {
    "/api/whoami": options.roles ? { ...WHOAMI, roles: options.roles } : WHOAMI,
    "/api/rules": { items: RULES },
    "/api/machines": { items: MACHINES },
    "/api/actors": { items: ACTORS },
    "/api/workflows": { items: WORKFLOWS },
    "/api/runs": { items: runsFor(now) },
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    calls.push(`${route.request().method()} ${url.pathname}`);
    if (url.pathname === "/api/events/stream") {
      // The live feed: no changes (a quiet store); reconnect in 10 s.
      await route.fulfill({
        status: 200,
        headers: { "content-type": "text/event-stream" },
        body: "retry: 10000\n\n",
      });
      return;
    }
    const history = url.pathname.match(/^\/api\/rules\/([^/]+)\/history$/);
    const body = history
      ? {
          items: runsFor(now)
            .filter((r) => r.rule_id === decodeURIComponent(history[1]))
            .map((r) => ({ kind: "run", at: r.created_at, ...r })),
        }
      : bodies[url.pathname];
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
