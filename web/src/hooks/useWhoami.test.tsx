import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { effectiveRole, useWhoami, resetWhoamiForTests } from "./useWhoami";
import { WHOAMI } from "../fixtures/rules-fixture";
import { mockFetch } from "../test/mockApi";

describe("useWhoami", () => {
  beforeEach(() => resetWhoamiForTests());
  afterEach(() => vi.unstubAllGlobals());

  it("reads identity from GET /api/whoami (the WhoAmI schema)", async () => {
    const { calls } = mockFetch({ "/api/whoami": { body: WHOAMI } });
    const { result } = renderHook(() => useWhoami());
    await waitFor(() => expect(result.current.status).toBe("signed-in"));
    expect(calls).toEqual(["/api/whoami"]);
    if (result.current.status !== "signed-in") throw new Error("unreachable");
    expect(result.current.displayName).toBe("ori");
    expect(result.current.kind).toBe("sso");
    expect(result.current.role).toBe("admin");
  });

  it("names a 401 as unauthenticated", async () => {
    mockFetch({
      "/api/whoami": { status: 401, body: { error: { code: "unauthenticated", message: "no", errors: [] } } },
    });
    const { result } = renderHook(() => useWhoami());
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
  });

  it("never mocks an identity: a missing route (404) is unavailable", async () => {
    mockFetch({});
    const { result } = renderHook(() => useWhoami());
    await waitFor(() => expect(result.current.status).toBe("unavailable"));
  });

  it("names a 5xx as unavailable", async () => {
    mockFetch({ "/api/whoami": { status: 503, body: {} } });
    const { result } = renderHook(() => useWhoami());
    await waitFor(() => expect(result.current.status).toBe("unavailable"));
  });

  it("reads once per session and shares the answer", async () => {
    const { calls } = mockFetch({ "/api/whoami": { body: WHOAMI } });
    const a = renderHook(() => useWhoami());
    const b = renderHook(() => useWhoami());
    await waitFor(() => expect(a.result.current.status).toBe("signed-in"));
    await waitFor(() => expect(b.result.current.status).toBe("signed-in"));
    expect(calls).toHaveLength(1);
  });
});

describe("effectiveRole", () => {
  it("is the highest of roles, viewer < editor < admin, in any order", () => {
    expect(effectiveRole(["editor", "viewer"])).toBe("editor");
    expect(effectiveRole(["admin", "viewer"])).toBe("admin");
    expect(effectiveRole(["viewer"])).toBe("viewer");
  });

  it("is null when no known role is held", () => {
    expect(effectiveRole([])).toBeNull();
    expect(effectiveRole(["owner"])).toBeNull();
  });
});
