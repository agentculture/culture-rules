import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch } from "../test/mockApi";
import { ApiError } from "./client";
import { listAsks } from "./rules";

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
