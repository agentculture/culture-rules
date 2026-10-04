import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch } from "../test/mockApi";
import { ApiError } from "./client";
import { listWorkflowDefs, purgeWorkflow, runWorkflow } from "./workflows";

const init = (fetchMock: ReturnType<typeof mockFetch>["fetchMock"], i = 0) =>
  (fetchMock.mock.calls[i] as unknown as [string, RequestInit])[1];

describe("workflows API: run, purge, include_deleted", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("POST /workflows/{id}/run sends {inputs} and answers the run doc", async () => {
    const doc = { id: "run-1", status: "running", rule_id: "adhoc:wf a", workflow_id: "wf a", steps: [] };
    const { fetchMock, calls } = mockFetch({ "/api/workflows/wf%20a/run": { status: 201, body: doc } });
    expect(await runWorkflow("wf a", { sha: "abc" })).toEqual(doc);
    expect(calls).toEqual(["/api/workflows/wf%20a/run"]);
    expect(init(fetchMock).method).toBe("POST");
    expect(JSON.parse(init(fetchMock).body as string)).toEqual({ inputs: { sha: "abc" } });
  });

  it("runWorkflow defaults to empty inputs", async () => {
    const { fetchMock } = mockFetch({ "/api/workflows/w/run": { status: 201, body: { id: "r" } } });
    await runWorkflow("w");
    expect(JSON.parse(init(fetchMock).body as string)).toEqual({ inputs: {} });
  });

  it("a 422 invalid_inputs is an ApiError with its code", async () => {
    mockFetch({
      "/api/workflows/w/run": {
        status: 422,
        body: { error: { code: "invalid_inputs", message: "bad", errors: [{ path: "inputs.sha", code: "required", message: "x" }] } },
      },
    });
    await expect(runWorkflow("w", {})).rejects.toMatchObject({ status: 422, code: "invalid_inputs" });
    await expect(runWorkflow("w", {})).rejects.toBeInstanceOf(ApiError);
  });

  it("POST /workflows/{id}/purge sends {apply} — dry-run unless applied", async () => {
    const { fetchMock, calls } = mockFetch({
      "/api/workflows/w/purge": { body: { collection: "workflows", id: "w", applied: false } },
    });
    expect(await purgeWorkflow("w", false)).toEqual({ collection: "workflows", id: "w", applied: false });
    await purgeWorkflow("w", true);
    expect(calls).toEqual(["/api/workflows/w/purge", "/api/workflows/w/purge"]);
    expect(init(fetchMock, 0).method).toBe("POST");
    expect(JSON.parse(init(fetchMock, 0).body as string)).toEqual({ apply: false });
    expect(JSON.parse(init(fetchMock, 1).body as string)).toEqual({ apply: true });
  });

  it("GET /workflows?include_deleted=true only when asked", async () => {
    const { calls } = mockFetch({ "/api/workflows": { body: { items: [] } } });
    await listWorkflowDefs();
    await listWorkflowDefs({ includeDeleted: true });
    expect(calls).toEqual(["/api/workflows", "/api/workflows?include_deleted=true"]);
  });
});
