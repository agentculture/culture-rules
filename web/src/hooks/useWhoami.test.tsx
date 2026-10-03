import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useWhoami, resetWhoamiForTests } from "./useWhoami";
import { WHOAMI } from "../fixtures/rules-fixture";
import { mockFetch } from "../test/mockApi";

describe("useWhoami", () => {
  beforeEach(() => resetWhoamiForTests());
  afterEach(() => vi.unstubAllGlobals());

  it("reads identity from GET /api/whoami", async () => {
    const { calls } = mockFetch({ "/api/whoami": { body: WHOAMI } });
    const { result } = renderHook(() => useWhoami());
    await waitFor(() => expect(result.current.status).toBe("signed-in"));
    expect(calls).toEqual(["/api/whoami"]);
    if (result.current.status !== "signed-in") throw new Error("unreachable");
    expect(result.current.displayName).toBe("ori");
    expect(result.current.whoami.role).toBe("admin");
    expect(result.current.mocked).toBe(false);
  });

  it("falls back to a flagged mock identity when the API has no /whoami route (404)", async () => {
    mockFetch({});
    const { result } = renderHook(() => useWhoami());
    await waitFor(() => expect(result.current.status).toBe("signed-in"));
    if (result.current.status !== "signed-in") throw new Error("unreachable");
    expect(result.current.mocked).toBe(true);
  });

  it("names a 401 as unauthenticated, not as a mock", async () => {
    mockFetch({
      "/api/whoami": { status: 401, body: { error: { code: "unauthenticated", message: "no", errors: [] } } },
    });
    const { result } = renderHook(() => useWhoami());
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
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
