import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Statistics from "../routes/Statistics";
import { resetAgentState } from "../agent-state/store";
import { LIVE_DEBOUNCE_MS, setLiveSourceFactory } from "../api/live";
import { FakeEventSource } from "../test/fakeEventSource";
import { defaultRoutes, mockFetch, type Routes } from "../test/mockApi";
import { STAT_MACHINES, STAT_RULES, STAT_WORKFLOWS, statRuns, statStatuses } from "./fixture";
import { STATUS_POLL_MS } from "./StatisticsBoard";

const NOW = Date.parse("2026-10-03T12:00:00Z");

function routes(): Routes {
  return {
    ...defaultRoutes(NOW),
    "/api/machines": { body: { items: STAT_MACHINES } },
    "/api/rules": { body: { items: STAT_RULES } },
    "/api/workflows": { body: { items: STAT_WORKFLOWS } },
    "/api/runs": { body: { items: statRuns(NOW) } },
    "/api/machines/status": { body: { items: statStatuses(NOW) } },
  };
}

/** The API's next answer: thor's queue grew to 7 and it took on a new step. */
function thorBusier(r: Routes) {
  const items = statStatuses(NOW).map((s) =>
    s.name === "thor"
      ? { ...s, queue_depth: 7, running: [...s.running, { step: "Deploy", workflow: "Ship" }] }
      : s,
  );
  r["/api/machines/status"] = { body: { items } };
}

const statusCalls = (calls: string[]) => calls.filter((c) => c.startsWith("/api/machines/status")).length;

function renderStats() {
  return render(
    <MemoryRouter initialEntries={["/statistics"]}>
      <Statistics />
    </MemoryRouter>,
  );
}

const lane = async (name: string) => within(await screen.findByRole("region", { name }));

describe("Statistics refreshes live (h33 / c49)", () => {
  beforeEach(() => {
    resetAgentState();
    FakeEventSource.reset();
    vi.useFakeTimers({ now: NOW, shouldAdvanceTime: true });
  });
  afterEach(() => {
    setLiveSourceFactory(undefined);
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("subscribes to machines, runs and heartbeats and refetches on a change, no reload", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    const r = routes();
    mockFetch(r);
    renderStats();
    expect((await lane("thor")).getByText("+4 queued")).toBeInTheDocument();
    const url = new URL(FakeEventSource.latest().url, "http://x");
    expect(url.pathname).toBe("/api/events/stream");
    expect(url.searchParams.get("collections")?.split(",").sort()).toEqual([
      "heartbeats",
      "machines",
      "runs",
    ]);

    thorBusier(r);
    act(() => FakeEventSource.latest().change("heartbeats", "thor"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_DEBOUNCE_MS + 5);
    });
    await waitFor(async () => expect((await lane("thor")).getByText("+7 queued")).toBeInTheDocument());
    expect((await lane("thor")).getByText("Deploy")).toBeInTheDocument();
  });

  it("polls GET /machines/status about every 10 s, so heartbeat load shows without a write", async () => {
    setLiveSourceFactory(null);
    const r = routes();
    const { calls } = mockFetch(r);
    renderStats();
    expect((await lane("thor")).getByText("+4 queued")).toBeInTheDocument();
    const before = statusCalls(calls);
    expect(STATUS_POLL_MS).toBeGreaterThanOrEqual(5_000);
    expect(STATUS_POLL_MS).toBeLessThanOrEqual(15_000);

    thorBusier(r);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(STATUS_POLL_MS + 10);
    });
    await waitFor(async () => expect((await lane("thor")).getByText("+7 queued")).toBeInTheDocument());
    expect(statusCalls(calls)).toBeGreaterThan(before);
  });

  it("flips a machine offline once its last heartbeat goes stale", async () => {
    setLiveSourceFactory(null);
    mockFetch(routes()); // the same answer every poll: spark's last_seen never moves
    renderStats();
    const spark = await screen.findByRole("region", { name: "spark" });
    expect(spark).toHaveAttribute("data-online", "true");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(4 * STATUS_POLL_MS);
    });
    await waitFor(() => expect(spark).toHaveAttribute("data-online", "false"));
    expect(within(spark).getByText(/^offline/)).toBeInTheDocument();
    expect(within(spark).getByText("not reachable")).toBeInTheDocument();
  });
});
