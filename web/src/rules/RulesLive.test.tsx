import { act, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Rules from "../routes/Rules";
import { resetAgentState } from "../agent-state/store";
import { LIVE_DEBOUNCE_MS, setLiveSourceFactory } from "../api/live";
import { SELECTED_RULE_ID } from "../fixtures/rules-fixture";
import { FakeEventSource } from "../test/fakeEventSource";
import { createFakeApi, fetchFor, withPendingAsk, type FakeApi } from "./fake-api";

let api: FakeApi;

function renderRules(path = `/rules/${SELECTED_RULE_ID}`) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/rules/:ruleId?" element={<Rules />} />
      </Routes>
    </MemoryRouter>,
  );
}

async function emit(collection: string, id: string) {
  act(() => FakeEventSource.latest().change(collection, id));
  await act(async () => {
    await new Promise((r) => setTimeout(r, LIVE_DEBOUNCE_MS + 30));
  });
}

beforeEach(() => {
  resetAgentState();
  FakeEventSource.reset();
  api = createFakeApi(Date.parse("2026-10-03T12:00:00Z"));
  vi.stubGlobal("fetch", fetchFor(api));
});
afterEach(() => {
  setLiveSourceFactory(undefined);
  vi.unstubAllGlobals();
});

describe("Rules tab live updates (h61 / c80)", () => {
  it("subscribes to rules, runs, asks and rule decisions", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    renderRules();
    await screen.findByRole("switch", { name: "Train batch enabled" });
    const url = new URL(FakeEventSource.latest().url, "http://x");
    expect(url.pathname).toBe("/api/events/stream");
    expect(url.searchParams.get("collections")?.split(",").sort()).toEqual([
      "asks",
      "rule_decisions",
      "rules",
      "runs",
    ]);
  });

  it("a rule toggled elsewhere shows here without a reload", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    renderRules();
    const sw = await screen.findByRole("switch", { name: "Train batch enabled" });
    expect(sw).toHaveAttribute("aria-checked", "true");
    api.rules.find((r) => r.id === "train-batch")!.enabled = false; // another editor's write
    await emit("rules", "train-batch");
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Train batch enabled" })).toHaveAttribute(
        "aria-checked",
        "false",
      ),
    );
  });

  it("a new ask on a run of this rule appears when runs/asks change", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    renderRules();
    await screen.findByRole("complementary", { name: "Last runs" });
    expect(screen.queryByRole("region", { name: "Waiting on you" })).toBeNull();
    withPendingAsk(api);
    await emit("asks", "ask_1");
    expect(await screen.findByRole("region", { name: "Waiting on you" })).toHaveTextContent(
      "Ship this build to production?",
    );
  });
});

describe("Last runs shows the rule's contextual history (h78 / c97)", () => {
  it("a superseded skip reads 'superseded by <rule name>' with an icon and a label", async () => {
    api.decisions = [
      {
        rule_id: SELECTED_RULE_ID,
        event_id: "evt_7",
        reason: "superseded_by",
        by: ["review-on-approve"],
        message: "superseded by review-on-approve",
        at: new Date(api.now - 30 * 60_000).toISOString(),
        host: "spark",
      },
    ];
    renderRules();
    const aside = await screen.findByRole("complementary", { name: "Last runs" });
    await waitFor(() => expect(within(aside).getAllByRole("listitem")).toHaveLength(5));
    const skip = within(aside).getByText("superseded by Review on approve").closest("li")!;
    expect(skip).toHaveAttribute("data-decision", "superseded_by");
    expect(skip.querySelector("svg")).not.toBeNull();
    expect(within(skip).getByText(/skipped/)).toBeInTheDocument();
    // newest first: the 4m run, then the 30m skip
    const items = within(aside).getAllByRole("listitem");
    expect(items[1]).toBe(skip);
    expect(api.calls.some((c) => c.path === `/rules/${SELECTED_RULE_ID}/history`)).toBe(true);
  });

  it("a decision record arriving live is shown without a reload", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    renderRules();
    const aside = await screen.findByRole("complementary", { name: "Last runs" });
    await waitFor(() => expect(within(aside).getAllByRole("listitem")).toHaveLength(4));
    api.decisions.push({
      rule_id: SELECTED_RULE_ID,
      event_id: "evt_8",
      reason: "superseded_by",
      by: ["review-on-approve"],
      message: "superseded by review-on-approve",
      at: new Date(api.now - 60_000).toISOString(),
      host: "thor",
    });
    await emit("rule_decisions", "d1");
    expect(await within(aside).findByText("superseded by Review on approve")).toBeInTheDocument();
  });
});

describe("Rules tab toggle in flight (#7)", () => {
  it("a double click while the toggle is in flight sends one request and disables the switch", async () => {
    const real = fetchFor(api);
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (/\/rules\/train-batch\/(enable|disable)$/.test(String(input))) await gate;
      return real(input, init);
    }) as typeof fetch);
    renderRules();
    const sw = await screen.findByRole("switch", { name: "Train batch enabled" });
    act(() => {
      sw.click();
      sw.click();
    });
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Train batch enabled" })).toHaveAttribute(
        "aria-disabled",
        "true",
      ),
    );
    release();
    await waitFor(() =>
      expect(screen.getByRole("switch", { name: "Train batch enabled" })).not.toHaveAttribute(
        "aria-disabled",
      ),
    );
    const toggles = api.calls.filter((c) => /\/rules\/train-batch\/(enable|disable)$/.test(c.path));
    expect(toggles).toHaveLength(1);
  });
});
