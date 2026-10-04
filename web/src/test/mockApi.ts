import { vi } from "vitest";
import { ACTORS, MACHINES, RULES, WHOAMI, WORKFLOWS, runsFor } from "../fixtures/rules-fixture";

export type Routes = Record<string, { status?: number; body?: unknown }>;

/** The fixture API as a `fetch` stub keyed by path (query string ignored). */
export function defaultRoutes(now = Date.now()): Routes {
  return {
    "/api/whoami": { body: WHOAMI },
    "/api/rules": { body: { items: RULES } },
    "/api/machines": { body: { items: MACHINES } },
    "/api/actors": { body: { items: ACTORS } },
    "/api/workflows": { body: { items: WORKFLOWS } },
    "/api/runs": { body: { items: runsFor(now) } },
  };
}

export function mockFetch(routes: Routes) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input.toString();
    calls.push(url);
    const path = url.split("?")[0];
    const route = routes[path];
    if (!route) {
      return new Response(
        JSON.stringify({ error: { code: "not_found", message: `no route ${path}`, errors: [] } }),
        { status: 404, headers: { "content-type": "application/json" } },
      );
    }
    return new Response(JSON.stringify(route.body ?? {}), {
      status: route.status ?? 200,
      headers: { "content-type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}
