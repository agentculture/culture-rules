import type { Page } from "@playwright/test";
import {
  ACTORS,
  EXPORT_RESULT,
  REPOS,
  REVIEW_PR_DESCRIBED,
  RUN_7,
  STARTED_RUN,
  WORKFLOW_DOCS,
  WORKFLOW_RULES,
  importPlan,
  workflowRunsFor,
} from "../../src/workflows/fixture";
import type { WorkflowDef } from "../../src/api/workflows";

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
 * and /repos, recording every call with its method and body. Workflows can
 * be created (409 on a taken id), enabled / disabled, soft-deleted (409
 * while a rule uses one) and restored; `start` replaces the stored list
 * (`[]` for the empty state).
 */
export const GONE_WORKFLOW: WorkflowDef = {
  ...WORKFLOW_DOCS[1],
  id: "old-flow",
  name: "Old flow",
  deleted_at: "2026-10-02T10:00:00Z",
  deleted_by: "ori",
  restorable_until: "2026-10-09T10:00:00Z",
};

/** What a workflow run started from the Run form produces once it finishes. */
export const FORM_RUN_OUTPUTS = { verdict: "approve", owner: "ori" };

/**
 * A stand-in for GET /workflows/{id}/describe on a hand-made workflow: its description and one
 * `step` segment per step (the step's own description, empty for a gap). The committed PR fixer
 * and newspaper use the real API's paragraphs instead (e2e/fixtures/describe-paragraphs.json).
 */
export function standInDescription(wf: WorkflowDef) {
  const steps = wf.steps ?? [];
  const segments = steps.map((s, i) => ({
    part: "step",
    step: s.id,
    name: s.name || s.id,
    depth: 0,
    phrase: i === 0 ? "First:" : "Then:",
    text: s.description ?? "",
  }));
  return {
    id: wf.id,
    kind: "workflow" as const,
    description: wf.description ?? "",
    paragraph: {
      segments,
      text: segments.map((s) => `${s.phrase} ${s.text}`.trim()).join(" "),
    },
    lines: steps.map((s, i) => `${i + 1} ${s.id}`),
    entries: steps.map((s, i) => ({ label: String(i + 1), text: s.kind, depth: 0, step: s.id })),
  };
}

export async function mockWorkflowsApi(
  page: Page,
  start: WorkflowDef[] = WORKFLOW_DOCS,
  /** Soft-deleted definitions, listed with `include_deleted=true` (purgeable by an admin). */
  gone: WorkflowDef[] = [],
): Promise<Recorded[]> {
  const calls: Recorded[] = [];
  const now = Date.now();
  // The server owns `revision`: a stored workflow carries one (1 when the fixture has none).
  let workflows: WorkflowDef[] = start.map((w) => ({ ...w, revision: w.revision ?? 1 }));
  const deleted = new Map<string, WorkflowDef>(gone.map((w) => [w.id, w]));
  let formRunPolls = 0;
  const used = new Set(WORKFLOW_RULES.map((r) => r.workflow?.id).filter(Boolean));

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
    const fail = (status: number, code: string, message: string) =>
      json(status, { error: { code, message, errors: [] } });
    if (path === "/api/workflows" && method === "GET") {
      const withGone = url.searchParams.get("include_deleted") === "true";
      return json(200, { items: withGone ? [...workflows, ...deleted.values()] : workflows });
    }
    if (path === "/api/workflows" && method === "POST") {
      if (!body?.id || !body?.name) return fail(422, "invalid_workflow", "id and name are required");
      if (workflows.some((w) => w.id === body.id) || deleted.has(body.id)) {
        return fail(409, "conflict", `workflows/${body.id} already exists`);
      }
      const stored = { ...body, version: 1, schema_version: "1.0", revision: 1 };
      workflows = [...workflows, stored];
      return json(201, stored);
    }
    const run = path.match(/^\/api\/workflows\/([^/]+)\/run$/);
    if (run && method === "POST") {
      return json(201, { ...STARTED_RUN, id: "run-9", workflow: { id: run[1], version: 3 } });
    }
    const purge = path.match(/^\/api\/workflows\/([^/]+)\/purge$/);
    if (purge && method === "POST") {
      if (!deleted.has(purge[1])) return fail(404, "not_found", `workflows/${purge[1]} is not deleted`);
      const applied = Boolean(body?.apply);
      if (applied) deleted.delete(purge[1]);
      return json(200, { collection: "workflows", id: purge[1], applied });
    }
    const verb = path.match(/^\/api\/workflows\/([^/]+)\/(enable|disable|restore)$/);
    if (verb && method === "POST") {
      const [, id, action] = verb;
      if (action === "restore") {
        const doc = deleted.get(id);
        if (!doc) return fail(404, "not_found", `workflows/${id} is not deleted`);
        deleted.delete(id);
        workflows = [...workflows, doc];
        return json(200, doc);
      }
      workflows = workflows.map((w) => (w.id === id ? { ...w, enabled: action === "enable" } : w));
      return json(200, workflows.find((w) => w.id === id));
    }
    const described = path.match(/^\/api\/workflows\/([^/]+)\/describe$/);
    if (described && method === "GET") {
      const stored = workflows.find((w) => w.id === described[1]);
      if (described[1] === REVIEW_PR_DESCRIBED.id) {
        const { lines, entries } = REVIEW_PR_DESCRIBED;
        return json(200, { ...(stored ? standInDescription(stored) : {}), ...REVIEW_PR_DESCRIBED, lines, entries });
      }
      if (!stored) return fail(404, "not_found", `workflows/${described[1]}`);
      return json(200, standInDescription(stored));
    }
    const one = path.match(/^\/api\/workflows\/([^/]+)$/);
    if (one && method === "DELETE") {
      if (used.has(one[1])) return fail(409, "conflict", `workflows/${one[1]} is used by a rule`);
      const doc = workflows.find((w) => w.id === one[1]);
      if (!doc) return fail(404, "not_found", `workflows/${one[1]} does not exist`);
      workflows = workflows.filter((w) => w.id !== one[1]);
      deleted.set(one[1], doc);
      return json(200, doc);
    }
    if (one && method === "PUT") {
      // The server owns `revision` (d7): a save must carry the one it read, else 409.
      const found = workflows.find((w) => w.id === one[1]);
      if (!found) return fail(404, "not_found", `workflows/${one[1]} does not exist`);
      const { expected_revision: expected, ...doc } = body ?? {};
      if (expected !== found.revision) {
        return json(409, {
          error: {
            code: "revision_conflict",
            message: "The workflow changed since it was read",
            errors: [
              { path: "expected_revision", code: "revision_conflict", message: "stale", current_revision: found.revision },
            ],
          },
        });
      }
      const saved = { ...doc, id: found.id, revision: (found.revision ?? 0) + 1 };
      workflows = workflows.map((w) => (w.id === one[1] ? saved : w));
      return json(200, saved);
    }
    if (one && method === "GET") return json(200, workflows.find((w) => w.id === one[1]));
    if (path === "/api/rules") return json(200, { items: WORKFLOW_RULES });
    if (path === "/api/actors") return json(200, { items: ACTORS });
    if (path === "/api/repos") return json(200, { items: REPOS });
    if (path === "/api/export" && method === "POST") {
      const applied = Boolean(body?.apply);
      return json(200, {
        repo: body?.repo,
        applied,
        committed: applied,
        pushed: false,
        commit: applied ? "0123456789abcdef" : null,
        changes: [{ kind: "workflows", id: "review-pr", path: "workflows/review-pr.json", action: "change" }],
      });
    }
    if (path === "/api/export") return json(200, EXPORT_RESULT);
    if (path === "/api/import") return json(200, importPlan(Boolean(body?.apply)));
    if (path === "/api/runs" && method === "POST") return json(201, STARTED_RUN);
    if (path === "/api/runs") return json(200, { items: workflowRunsFor(now) });
    if (path === "/api/runs/run-7") return json(200, RUN_7);
    if (path === "/api/runs/run-8") return json(200, STARTED_RUN);
    if (path === "/api/runs/run-9") {
      // Running on the first read, finished (with its exported outputs) on the next.
      formRunPolls += 1;
      return json(
        200,
        formRunPolls < 2
          ? { ...STARTED_RUN, id: "run-9" }
          : { ...STARTED_RUN, id: "run-9", status: "succeeded", finished_at: "2026-10-03T12:01:00Z", outputs: FORM_RUN_OUTPUTS },
      );
    }
    return route.fallback();
  });
  return calls;
}
