import { afterEach, describe, expect, it, vi } from "vitest";
import { API_ROOT, ApiError, listRules, listRuns } from "./client";
import { RULES } from "../fixtures/rules-fixture";
import { mockFetch } from "../test/mockApi";

describe("api client", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is same-origin under /api — never an absolute remote URL", () => {
    expect(API_ROOT).toBe("/api");
  });

  it("unwraps the {items} list envelope", async () => {
    mockFetch({ "/api/rules": { body: { items: RULES } } });
    await expect(listRules()).resolves.toHaveLength(RULES.length);
  });

  it("passes run filters as a query string", async () => {
    const { calls } = mockFetch({ "/api/runs": { body: { items: [] } } });
    await listRuns({ rule_id: "build-and-publish", limit: 4 });
    expect(calls).toEqual(["/api/runs?rule_id=build-and-publish&limit=4"]);
  });

  it("surfaces the error envelope's code and message", async () => {
    mockFetch({
      "/api/rules": {
        status: 409,
        body: { error: { code: "conflict", message: "stale version", errors: [] } },
      },
    });
    const err = await listRules().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 409, code: "conflict", message: "stale version" });
  });

  it("never attaches a credential header", async () => {
    const { fetchMock } = mockFetch({ "/api/rules": { body: { items: [] } } });
    await listRules();
    const init = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit | undefined;
    const headers = new Headers(init?.headers);
    expect(headers.has("authorization")).toBe(false);
    expect(headers.has("x-culture-identity")).toBe(false);
  });
});
