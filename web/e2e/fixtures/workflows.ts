import type { Page } from "@playwright/test";
import {
  ACTORS,
  EXPORT_RESULT,
  REPOS,
  RUN_7,
  STARTED_RUN,
  WORKFLOW_DOCS,
  WORKFLOW_RULES,
  importPlan,
  workflowRunsFor,
} from "../../src/workflows/fixture";

export interface Recorded {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

/**
 * The Workflows tab's API on top of e2e/fixtures/api.ts (compose: call
 * `mockApi(page)` first; routes registered later win). Serves full workflow
 * definitions, actors, the persisted run state of run-7, /export, /import
 * and the PLANNED /repos, recording every call with its method and body.
 */
export async function mockWorkflowsApi(page: Page): Promise<Recorded[]> {
  const calls: Recorded[] = [];
  const now = Date.now();
  let workflows = WORKFLOW_DOCS.map((w) => ({ ...w }));

  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const raw = request.postData();
    const body = raw ? JSON.parse(raw) : null;
    calls.push({ method, path: url.pathname, search: url.search, body });
    const json = (status: number, data: unknown) =>
      route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });

    const path = url.pathname;
    if (path === "/api/workflows" && method === "GET") return json(200, { items: workflows });
    const one = path.match(/^\/api\/workflows\/([^/]+)$/);
    if (one && method === "PUT") {
      workflows = workflows.map((w) => (w.id === one[1] ? body : w));
      return json(200, body);
    }
    if (one && method === "GET") return json(200, workflows.find((w) => w.id === one[1]));
    if (path === "/api/rules") return json(200, { items: WORKFLOW_RULES });
    if (path === "/api/actors") return json(200, { items: ACTORS });
    if (path === "/api/repos") return json(200, { items: REPOS });
    if (path === "/api/export") return json(200, EXPORT_RESULT);
    if (path === "/api/import") return json(200, importPlan(Boolean(body?.apply)));
    if (path === "/api/runs" && method === "POST") return json(201, STARTED_RUN);
    if (path === "/api/runs") return json(200, { items: workflowRunsFor(now) });
    if (path === "/api/runs/run-7") return json(200, RUN_7);
    if (path === "/api/runs/run-8") return json(200, STARTED_RUN);
    return route.fallback();
  });
  return calls;
}
