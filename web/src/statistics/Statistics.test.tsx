import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Statistics from "../routes/Statistics";
import { getAgentState, resetAgentState } from "../agent-state/store";
import * as client from "../api/client";
import { defaultRoutes, mockFetch, type Routes } from "../test/mockApi";
import {
  HOURS,
  STAT_MACHINES,
  STAT_RULES,
  STAT_WORKFLOWS,
  statRuns,
  statStatuses,
} from "./fixture";

const NOW = Date.parse("2026-10-03T12:00:00Z");

function routes(extra: Routes = {}): Routes {
  return {
    ...defaultRoutes(NOW),
    "/api/machines": { body: { items: STAT_MACHINES } },
    "/api/rules": { body: { items: STAT_RULES } },
    "/api/workflows": { body: { items: STAT_WORKFLOWS } },
    "/api/runs": { body: { items: statRuns(NOW) } },
    "/api/machines/status": { body: { items: statStatuses(NOW) } },
    ...extra,
  };
}

function renderStats() {
  return render(
    <MemoryRouter initialEntries={["/statistics"]}>
      <Statistics />
    </MemoryRouter>,
  );
}

const lane = async (name: string) => within(await screen.findByRole("region", { name }));

describe("Statistics board (Chosen — Statistics)", () => {
  beforeEach(() => {
    resetAgentState();
    vi.useFakeTimers({ toFake: ["Date"], now: NOW });
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("shows a lane for every enrolled machine, offline ones included", async () => {
    mockFetch(routes());
    renderStats();
    for (const m of STAT_MACHINES) expect(await screen.findByRole("region", { name: m.name })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Statistics" })).toBeInTheDocument();
  });

  it("each lane shows load, running steps, queue depth and ok/failed with icon + label", async () => {
    mockFetch(routes());
    renderStats();
    const thor = await lane("thor");
    expect(thor.getByText("CPU")).toBeInTheDocument();
    expect(thor.getByText("71%")).toBeInTheDocument();
    expect(thor.getByText("92%")).toBeInTheDocument();
    expect(thor.getByText("Review")).toBeInTheDocument();
    expect(thor.getByText("Train batch")).toBeInTheDocument();
    expect(thor.getByText("+4 queued")).toBeInTheDocument();
    const okLabel = thor.getByText("ok");
    expect(okLabel.parentElement?.querySelector("svg")).not.toBeNull();
    const failedLabel = thor.getByText("failed");
    expect(failedLabel.parentElement?.querySelector("svg")).not.toBeNull();
    expect(failedLabel.parentElement).toHaveTextContent("5 failed");
    const spark2 = await lane("spark2");
    expect(spark2.getByText("idle")).toBeInTheDocument();
    expect(spark2.queryByText(/queued/)).toBeNull();
  });

  it("an offline host renders offline: since when, n/a load, not reachable", async () => {
    mockFetch(routes());
    renderStats();
    const orin = await lane("orin");
    expect(orin.getByText("offline 2h")).toBeInTheDocument();
    expect(orin.getByText("not reachable")).toBeInTheDocument();
    expect(orin.getAllByText("n/a")).toHaveLength(3);
    expect(screen.getByRole("region", { name: "orin" })).toHaveAttribute("data-online", "false");
    expect(screen.getByRole("region", { name: "spark" })).toHaveAttribute("data-online", "true");
  });

  it("draws 24 runs-per-hour bars with an accessible summary, and switches range 1h/24h/7d", async () => {
    mockFetch(routes());
    const user = userEvent.setup();
    renderStats();
    const spark = await screen.findByRole("img", { name: "spark runs per hour, last 24 hours, peak 11" });
    expect(spark.querySelectorAll("[data-bar]")).toHaveLength(24);

    const group = screen.getByRole("radiogroup", { name: "Time range" });
    expect(within(group).getByRole("radio", { name: "24h" })).toHaveAttribute("aria-checked", "true");
    await user.click(within(group).getByRole("radio", { name: "1h" }));
    expect(within(group).getByRole("radio", { name: "1h" })).toHaveAttribute("aria-checked", "true");
    const hour = await screen.findByRole("img", { name: "spark runs per 5 min, last hour, peak 1" });
    expect(hour.querySelectorAll("[data-bar]")).toHaveLength(12);
    await user.click(within(group).getByRole("radio", { name: "7d" }));
    const week = await screen.findByRole("img", { name: /^spark runs per day, last 7 days, peak \d+$/ });
    expect(week.querySelectorAll("[data-bar]")).toHaveLength(7);
    expect(getAgentState()).toMatchObject({ statistics: { range: "7d" } });
  });

  it("arrow keys move the range selection (radiogroup keyboard contract)", async () => {
    mockFetch(routes());
    renderStats();
    const group = await screen.findByRole("radiogroup", { name: "Time range" });
    const checked = within(group).getByRole("radio", { name: "24h" });
    checked.focus();
    fireEvent.keyDown(checked, { key: "ArrowRight" });
    await waitFor(() =>
      expect(within(group).getByRole("radio", { name: "7d" })).toHaveAttribute("aria-checked", "true"),
    );
  });

  it("hovering a bar shows a tooltip with its runs", async () => {
    mockFetch(routes());
    const user = userEvent.setup();
    renderStats();
    const spark = await screen.findByRole("img", { name: /^spark runs per hour/ });
    expect(screen.queryByRole("tooltip")).toBeNull();
    const bars = spark.querySelectorAll("[data-bar]");
    await user.hover(bars[14]);
    const tip = await screen.findByRole("tooltip");
    expect(tip).toHaveTextContent(`${HOURS.spark[14]} runs`);
    await user.unhover(bars[14]);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("'Show as table' swaps the lanes for tables carrying the same numbers", async () => {
    mockFetch(routes());
    const user = userEvent.setup();
    renderStats();
    await screen.findByRole("region", { name: "spark" });
    await user.click(screen.getByRole("button", { name: "Show as table" }));
    expect(screen.queryByRole("region", { name: "spark" })).toBeNull();
    const summary = screen.getByRole("table", { name: "Machines" });
    const rows = within(summary).getAllByRole("row");
    expect(rows).toHaveLength(1 + 4);
    const orin = within(summary).getByRole("row", { name: /orin/ });
    expect(orin).toHaveTextContent("offline 2h");
    expect(within(summary).getByRole("row", { name: /thor/ })).toHaveTextContent("+4 queued");
    const perHour = screen.getByRole("table", { name: "Runs per hour" });
    expect(within(perHour).getAllByRole("row")).toHaveLength(1 + 24);
    await user.click(screen.getByRole("button", { name: "Show as lanes" }));
    expect(screen.getByRole("region", { name: "spark" })).toBeInTheDocument();
  });

  it("reports ready in #agent-state with the machines it shows", async () => {
    mockFetch(routes());
    renderStats();
    await screen.findByRole("region", { name: "orin" });
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    const s = getAgentState() as unknown as {
      tab: string;
      view_ready: boolean;
      errors: string[];
      statistics: { machines: string[]; offline: string[]; range: string; view: string };
    };
    expect(s.tab).toBe("statistics");
    expect(s.view_ready).toBe(true);
    expect(s.errors).toEqual([]);
    expect(s.statistics).toEqual({
      machines: ["spark", "thor", "spark2", "orin"],
      offline: ["orin"],
      range: "24h",
      view: "lanes",
      source: "machines/status",
    });
  });

  it("falls back to runs when /machines/status fails, says so and reports the failure", async () => {
    mockFetch(routes({ "/api/machines/status": { status: 503, body: { error: { code: "down", message: "status down", errors: [] } } } }));
    renderStats();
    const thor = await lane("thor");
    expect(thor.getAllByText("n/a")).toHaveLength(3);
    expect(screen.getByText(/live load and queue depth are not available/i)).toBeInTheDocument();
    expect(getAgentState().statistics?.source).toBe("runs");
    expect(getAgentState().errors).toEqual(["status down"]);
  });

  it("a load that fails while being applied is an alert and still reaches ready", async () => {
    mockFetch(routes());
    // A rejection reason with no string form: describing it throws inside the load's .then.
    vi.spyOn(client, "listMachines").mockRejectedValue(Object.create(null));
    renderStats();
    expect(await screen.findByRole("alert")).toHaveTextContent(/primitive/i);
    await waitFor(() => expect(getAgentState().view_ready).toBe(true));
  });

  it("a failed load is an alert and still reaches ready", async () => {
    mockFetch(routes({ "/api/machines": { status: 500, body: { error: { code: "boom", message: "store down", errors: [] } } } }));
    renderStats();
    expect(await screen.findByRole("alert")).toHaveTextContent("store down");
    await waitFor(() => expect(getAgentState().view_ready).toBe(true));
    expect(getAgentState().errors.length).toBeGreaterThan(0);
  });
});
