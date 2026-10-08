import type { Page } from "@playwright/test";
import {
  ACTORS,
  MACHINES,
  RULES,
  VARIABLES,
  VARIABLE_REFS,
  VARIABLE_VERSIONS,
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
    "/api/variables": { items: VARIABLES },
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
    const described = url.pathname.match(/^\/api\/rules\/([^/]+)\/describe$/);
    if (described) {
      const rule = RULES.find((r) => r.id === decodeURIComponent(described[1]));
      if (rule) {
        // A stand-in for culture_rules/model/describe.py: the trigger and the action, in words.
        const params = (rule.trigger.params ?? {}) as Record<string, unknown>;
        const entries = [
          { label: "When", text: String(params.type ?? rule.trigger.kind), depth: 0 },
          { label: "Then", text: rule.action.kind, depth: 0 },
        ];
        const lines = entries.map((e) => `${e.label} ${e.text}`);
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({ id: rule.id, kind: "rule", lines, entries }),
        });
        return;
      }
    }
    const history = url.pathname.match(/^\/api\/rules\/([^/]+)\/history$/);
    const variable = url.pathname.match(/^\/api\/variables\/([^/]+)\/(history|refs)$/);
    const body = variable
      ? { items: variable[2] === "history" ? VARIABLE_VERSIONS : VARIABLE_REFS }
      : history
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
