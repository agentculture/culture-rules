import { afterEach, describe, expect, it, vi } from "vitest";
import { mockFetch } from "../test/mockApi";
import { getMachineStatuses } from "./statistics";
import { statStatuses } from "../statistics/fixture";

describe("statistics API: machine status", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GET /machines/status unwraps the item list", async () => {
    const items = statStatuses(Date.parse("2026-10-03T12:00:00Z"));
    const { calls } = mockFetch({ "/api/machines/status": { body: { items } } });
    expect(await getMachineStatuses()).toEqual(items);
    expect(calls).toEqual(["/api/machines/status"]);
  });

  it("a 404 is an error, not a silent 'endpoint absent' (the route exists)", async () => {
    mockFetch({});
    await expect(getMachineStatuses()).rejects.toMatchObject({ status: 404 });
  });
});
