import { afterEach, describe, expect, it, vi } from "vitest";
import {
  API_ROOT,
  ApiError,
  REQUEST_TIMEOUT_MS,
  getJson,
  listRules,
  listRuns,
  request,
} from "./client";
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

  it("passes the workflow and host run filters", async () => {
    const { calls } = mockFetch({ "/api/runs": { body: { items: [] } } });
    await listRuns({ workflow_id: "review-pr", host: "thor", limit: 50 });
    expect(calls).toEqual(["/api/runs?workflow_id=review-pr&host=thor&limit=50"]);
  });

  it("exports the shared request helpers the tab adapters build on", async () => {
    const { fetchMock, calls } = mockFetch({ "/api/rules": { body: { id: "r1" } } });
    await expect(request("POST", "/rules", { id: "r1" })).resolves.toEqual({ id: "r1" });
    const init = (fetchMock.mock.calls[0] as unknown[])[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("content-type")).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({ id: "r1" });
    await expect(getJson("/rules")).resolves.toEqual({ id: "r1" });
    expect(calls).toEqual(["/api/rules", "/api/rules"]);
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
  it("turns a request that never answers into a timeout error, so a view still settles", async () => {
    // CI finding: with no API behind the preview proxy the request hung and the
    // Rules view stayed "loading" forever (#agent-state never reached ready).
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      ),
    );
    const pending = getJson("/rules").catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS + 1);
    const err = await pending;
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).code).toBe("timeout");
    vi.useRealTimers();
  });

  it("still honours a caller's own abort signal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            );
          }),
      ),
    );
    const ctl = new AbortController();
    const pending = getJson("/rules", ctl.signal).catch((e: unknown) => e);
    ctl.abort();
    const err = await pending;
    expect((err as ApiError).code).toBe("aborted");
  });
});
