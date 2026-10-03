import type { Page } from "@playwright/test";
import { ACTORS } from "../../src/actors/actors-fixture";

export interface ActorCall {
  method: string;
  path: string;
  body: unknown;
}

/**
 * The /actors routes of the culture-rules API (api/openapi.json) as a small
 * stateful stub. Compose after `mockApi(page)`: Playwright runs the most
 * recently registered matching route first, so this one answers /actors*
 * and everything else falls through to the shared fixture.
 */
export async function mockActorsApi(page: Page): Promise<ActorCall[]> {
  const calls: ActorCall[] = [];
  let actors = structuredClone(ACTORS) as unknown as Record<string, unknown>[];
  const json = (status: number, body: unknown) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/actors**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const raw = request.postData();
    const body = raw ? JSON.parse(raw) : undefined;
    calls.push({ method, path: url.pathname, body });
    const m = url.pathname.match(/^\/api\/actors(?:\/([^/]+)(?:\/(enable|disable))?)?$/);
    if (!m) return route.fulfill(json(404, { error: { code: "not_found", message: url.pathname, errors: [] } }));
    const [, id, verb] = m;
    if (!id) {
      if (method === "GET") return route.fulfill(json(200, { items: actors }));
      actors.push(body);
      return route.fulfill(json(201, body));
    }
    const found = actors.find((a) => a.id === id);
    if (!found) return route.fulfill(json(404, { error: { code: "not_found", message: "no such actor", errors: [] } }));
    if (verb) {
      found.enabled = verb === "enable";
      return route.fulfill(json(200, found));
    }
    if (method === "PUT") {
      Object.assign(found, body);
      return route.fulfill(json(200, found));
    }
    actors = actors.filter((a) => a.id !== id);
    return route.fulfill(json(200, { id, deleted: true }));
  });
  return calls;
}
