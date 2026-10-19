import { afterEach, describe, expect, it, vi } from "vitest";
import {
  disableActor,
  enrolModels,
  getActorReferences,
  getModelGateways,
  inUseReferrers,
  isMissingRoute,
  parseModelGateways,
  repointWorkflowActor,
} from "./actors";
import { ApiError } from "./client";

function stubFetch(status: number, body: unknown) {
  const fetchMock = vi.fn(async () =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const callOf = (fetchMock: ReturnType<typeof stubFetch>) => {
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  return { url, method: init.method, body: init.body ? JSON.parse(String(init.body)) : undefined };
};

describe("model-actor routes", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("enrolModels POSTs the request body to /actors/enrol-models", async () => {
    const fetchMock = stubFetch(200, { dry_run: true, gateways: [], changes: [], also_served_by: {}, unseen: [] });
    await enrolModels({ gateways: ["spark"], match: ["qwen*"], pools: ["qwen3=gpu"], dry_run: true });
    expect(callOf(fetchMock)).toEqual({
      url: "/api/actors/enrol-models",
      method: "POST",
      body: { gateways: ["spark"], match: ["qwen*"], pools: ["qwen3=gpu"], dry_run: true },
    });
  });

  it("getActorReferences GETs /actors/{id}/references", async () => {
    const fetchMock = stubFetch(200, { actor_id: "a b", items: [] });
    expect(await getActorReferences("a b")).toEqual({ actor_id: "a b", items: [] });
    expect(callOf(fetchMock).url).toBe("/api/actors/a%20b/references");
  });

  it("repointWorkflowActor POSTs from/to/expected_revision and applies (dry_run false)", async () => {
    const fetchMock = stubFetch(200, { workflow_id: "w", dry_run: false });
    await repointWorkflowActor("w", { from: "a", to: "b", expected_revision: 3 });
    expect(callOf(fetchMock)).toEqual({
      url: "/api/workflows/w/repoint-actor",
      method: "POST",
      body: { from: "a", to: "b", expected_revision: 3, force: false, dry_run: false },
    });
  });

  it("disableActor adds ?force=true only when forced", async () => {
    let fetchMock = stubFetch(200, {});
    await disableActor("x");
    expect(callOf(fetchMock).url).toBe("/api/actors/x/disable");
    fetchMock = stubFetch(200, {});
    await disableActor("x", true);
    expect(callOf(fetchMock).url).toBe("/api/actors/x/disable?force=true");
  });

  it("getModelGateways reads the model_gateways variable as machine=URL pairs", async () => {
    stubFetch(200, { name: "model_gateways", value: ["spark=http://spark:4000", "thor=http://thor:4000/v1"] });
    expect(await getModelGateways()).toEqual([
      { machine: "spark", url: "http://spark:4000" },
      { machine: "thor", url: "http://thor:4000/v1" },
    ]);
  });

  it("getModelGateways answers null when the variable is unset (a not_found envelope)", async () => {
    stubFetch(404, { error: { code: "not_found", message: "variables/model_gateways does not exist", errors: [] } });
    expect(await getModelGateways()).toBeNull();
  });
});

describe("parseModelGateways", () => {
  it("splits at the first '=' and trims the machine", () => {
    expect(parseModelGateways([" spark =http://a=b"])).toEqual([{ machine: "spark", url: "http://a=b" }]);
  });

  it("refuses an object or an item without '=' (the server's d4 form only)", () => {
    expect(parseModelGateways({ spark: "http://x" })).toBeNull();
    expect(parseModelGateways(["spark"])).toBeNull();
    expect(parseModelGateways("spark=http://x")).toBeNull();
  });
});

describe("isMissingRoute (an older server, c33)", () => {
  it("is true for FastAPI's generic 404/405 (no envelope)", () => {
    expect(isMissingRoute(new ApiError(404, "http_error", "404 Not Found"))).toBe(true);
    expect(isMissingRoute(new ApiError(405, "http_error", "405 Method Not Allowed"))).toBe(true);
  });

  it("is true for the web-build catch-all's 'no such API route' envelope", () => {
    expect(isMissingRoute(new ApiError(404, "not_found", "no such API route"))).toBe(true);
    expect(isMissingRoute(new ApiError(404, "not_found", "no such route"))).toBe(true);
  });

  it("is false for a real not_found envelope and for other failures", () => {
    expect(isMissingRoute(new ApiError(404, "not_found", "actors/x does not exist"))).toBe(false);
    expect(isMissingRoute(new ApiError(409, "actor_in_use", "in use"))).toBe(false);
    expect(isMissingRoute(new Error("boom"))).toBe(false);
  });
});

describe("inUseReferrers", () => {
  it("reads the actor_in_use errors[] as workflows and rules", () => {
    const err = new ApiError(409, "actor_in_use", "actor_in_use: x is used by ...", [
      { path: "workflows/summarise", code: "actor_in_use", message: "workflow summarise uses x" },
      { path: "rules/nightly", code: "actor_in_use", message: "rule nightly uses x" },
      { path: "workflows/summarise", code: "actor_in_use", message: "workflow summarise uses x again" },
    ]);
    expect(inUseReferrers(err)).toEqual([
      { kind: "workflow", id: "summarise" },
      { kind: "rule", id: "nightly" },
    ]);
  });

  it("is null for anything but an actor_in_use refusal", () => {
    expect(inUseReferrers(new ApiError(409, "conflict", "x"))).toBeNull();
    expect(inUseReferrers(new Error("x"))).toBeNull();
  });
});
