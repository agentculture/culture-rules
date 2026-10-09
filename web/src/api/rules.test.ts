import { afterEach, describe, expect, it, vi } from "vitest";
import { RULES } from "../fixtures/rules-fixture";
import { createFakeApi, fetchFor, withActiveRuns } from "../rules/fake-api";
import { mockFetch } from "../test/mockApi";
import { ApiError } from "./client";
import { listAsks, setRuleEnabled, updateRule } from "./rules";

const ASK = { id: "ask_1", run_id: "run-1", question: "Ship it?", options: ["yes", "no"], status: "open" };

describe("rules API: asks", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GET /asks?run_id=&status=open lists the run's open asks", async () => {
    const { calls } = mockFetch({ "/api/asks": { body: { items: [ASK] } } });
    expect(await listAsks("run-1")).toEqual([ASK]);
    expect(calls).toEqual(["/api/asks?run_id=run-1&status=open"]);
  });

  it("a missing route is an error now that the API serves /asks (no 404 fallback)", async () => {
    mockFetch({});
    await expect(listAsks("run-1")).rejects.toBeInstanceOf(ApiError);
  });
});

/** Moved here unchanged from src/rules/StopRuns.test.tsx (it never needed the Rules tab). */
describe("rules API: active runs are response-only", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("a disable answer's active_runs are split off the rule", async () => {
    const api = withActiveRuns(createFakeApi(Date.parse("2026-10-03T12:00:00Z")), "train-batch", 2);
    vi.stubGlobal("fetch", fetchFor(api));
    const rule = structuredClone(RULES.find((r) => r.id === "train-batch")!);
    const answer = await setRuleEnabled(rule, false);
    expect(answer.activeRunsTotal).toBe(2);
    expect(answer.activeRuns.map((r) => r.id)).toEqual(["run-active-1", "run-active-2"]);
    expect(answer.rule).not.toHaveProperty("active_runs");
    expect(answer.rule).not.toHaveProperty("active_runs_total");
    expect(answer.rule.enabled).toBe(false);
  });

  it("a save drops them too, so they are never sent back", async () => {
    vi.stubGlobal("fetch", fetchFor(createFakeApi(Date.parse("2026-10-03T12:00:00Z"))));
    const rule = structuredClone(RULES.find((r) => r.id === "train-batch")!);
    const saved = await updateRule({ ...rule, active_runs: [], active_runs_total: 0 } as typeof rule);
    expect(saved).not.toHaveProperty("active_runs");
  });
});
