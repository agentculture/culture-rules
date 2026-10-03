import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch } from "../test/mockApi";
import {
  exportDefinitions,
  exportToRepo,
  getRun,
  importDefinitions,
  importFromRepo,
  listRepos,
  listWorkflowRuns,
  putWorkflowDef,
  startRun,
} from "./workflows";
import { EXPORT_RESULT, REPOS, REVIEW_PR, RUN_7, importPlan, workflowRunsFor } from "../workflows/fixture";

const init = (fetchMock: ReturnType<typeof mockFetch>["fetchMock"], i = 0) =>
  (fetchMock.mock.calls[i] as unknown as [string, RequestInit])[1];

describe("workflows API client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GET /export?format=json", async () => {
    const { fetchMock, calls } = mockFetch({ "/api/export": { body: EXPORT_RESULT } });
    expect(await exportDefinitions()).toEqual(EXPORT_RESULT);
    expect(calls).toEqual(["/api/export?format=json"]);
    expect(init(fetchMock).method).toBe("GET");
  });

  it("POST /import sends {files, apply} — dry-run unless applied", async () => {
    const { fetchMock, calls } = mockFetch({ "/api/import": { body: importPlan(false) } });
    await importDefinitions({ "workflows/a.json": "{}" }, false);
    expect(calls).toEqual(["/api/import"]);
    expect(init(fetchMock).method).toBe("POST");
    expect(JSON.parse(init(fetchMock).body as string)).toEqual({
      files: { "workflows/a.json": "{}" },
      apply: false,
    });
  });

  it("POST /export writes into a repo — a dry-run plan unless applied", async () => {
    const plan = { repo: "agentculture/workflows", applied: false, committed: false, pushed: false, commit: null, changes: [] };
    const { fetchMock, calls } = mockFetch({ "/api/export": { body: plan } });
    expect(await exportToRepo("agentculture/workflows", false)).toEqual(plan);
    expect(calls).toEqual(["/api/export"]);
    expect(init(fetchMock).method).toBe("POST");
    expect(JSON.parse(init(fetchMock).body as string)).toEqual({ repo: "agentculture/workflows", apply: false });
  });

  it("POST /import with a repo reads the definitions from it", async () => {
    const { fetchMock } = mockFetch({ "/api/import": { body: importPlan(false) } });
    await importFromRepo("agentculture/workflows", true);
    expect(JSON.parse(init(fetchMock).body as string)).toEqual({ repo: "agentculture/workflows", apply: true });
  });

  it("GET /repos lists repositories", async () => {
    const { calls } = mockFetch({ "/api/repos": { body: { items: REPOS } } });
    expect(await listRepos()).toEqual(REPOS);
    expect(calls).toEqual(["/api/repos"]);
  });

  it("PUT /workflows/{id} replaces a definition", async () => {
    const { fetchMock, calls } = mockFetch({ "/api/workflows/review-pr": { body: REVIEW_PR } });
    await putWorkflowDef(REVIEW_PR);
    expect(calls).toEqual(["/api/workflows/review-pr"]);
    expect(init(fetchMock).method).toBe("PUT");
  });

  it("runs: list filtered to one workflow, one by id, start through a rule", async () => {
    const now = Date.parse("2026-10-03T12:00:00Z");
    const { fetchMock, calls } = mockFetch({
      "/api/runs": { body: { items: workflowRunsFor(now) } },
      "/api/runs/run-7": { body: RUN_7 },
    });
    const runs = await listWorkflowRuns("review-pr");
    expect(runs.map((r) => r.id)).toEqual(["run-7", "run-6"]);
    expect((await getRun("run-7")).steps).toHaveLength(5);
    await startRun("review-on-approve");
    expect(calls).toEqual(["/api/runs?workflow_id=review-pr&limit=50", "/api/runs/run-7", "/api/runs"]);
    expect(JSON.parse(init(fetchMock, 2).body as string)).toEqual({ rule_id: "review-on-approve" });
  });

  it("an error envelope becomes an ApiError with its message", async () => {
    mockFetch({ "/api/import": { status: 422, body: { error: { code: "invalid", message: "bad file", errors: [] } } } });
    await expect(importDefinitions({}, false)).rejects.toMatchObject({ status: 422, message: "bad file" });
  });
});
