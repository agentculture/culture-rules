import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Rules from "./RulesBoard.legacy";
import { resetAgentState } from "../agent-state/store";
import { setRuleEnabled, updateRule } from "../api/rules";
import { RULES } from "../fixtures/rules-fixture";
import { createFakeApi, fetchFor, withActiveRuns, type FakeApi } from "./fake-api";

/** d17: disabling a rule with runs still going asks 'Stop N current runs?'. */

let api: FakeApi;

function renderRules(path = "/rules/train-batch") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/rules/:ruleId?" element={<Rules />} />
      </Routes>
    </MemoryRouter>,
  );
}

const sent = (method: string, path: string) =>
  api.calls.filter((c) => c.method === method && c.path === path);

async function disableTrainBatch(user: ReturnType<typeof userEvent.setup>) {
  const sw = await screen.findByRole("switch", { name: "Train batch enabled" });
  await user.click(sw);
  await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
  return sw;
}

beforeEach(() => {
  resetAgentState();
  api = createFakeApi(Date.parse("2026-10-03T12:00:00Z"));
  vi.stubGlobal("fetch", fetchFor(api));
});
afterEach(() => vi.unstubAllGlobals());

describe("stop current runs on disable", () => {
  it("asks 'Stop N current runs?' without taking focus, and Approve stops them", async () => {
    withActiveRuns(api, "train-batch", 2);
    const user = userEvent.setup();
    renderRules();
    const sw = await disableTrainBatch(user);

    const notice = await screen.findByText(/Stop 2 current runs\?/);
    expect(notice).toHaveTextContent("Train batch is off. Stop 2 current runs?");
    expect(sw).toHaveFocus(); // non-modal: the toggle keeps focus
    expect(sent("POST", "/rules/train-batch/stop-runs")).toHaveLength(0); // never automatic

    await user.click(screen.getByRole("button", { name: /^Approve/ }));
    expect(await screen.findByText("Stopped 2 runs of Train batch.")).toBeInTheDocument();
    const stop = sent("POST", "/rules/train-batch/stop-runs");
    expect(stop).toHaveLength(1);
    expect(stop[0].body).toEqual({ apply: true });
    expect(api.activeRuns["train-batch"]).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Stopped 2 runs/)).not.toBeInTheDocument();
  });

  it("Keep running dismisses it and stops nothing", async () => {
    withActiveRuns(api, "train-batch", 1);
    const user = userEvent.setup();
    renderRules();
    await disableTrainBatch(user);
    expect(await screen.findByText(/Stop 1 current run\?/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep running" }));
    expect(screen.queryByText(/current run/)).not.toBeInTheDocument();
    expect(sent("POST", "/rules/train-batch/stop-runs")).toHaveLength(0);
    expect(api.activeRuns["train-batch"]).toHaveLength(1);
    expect(api.rules.find((r) => r.id === "train-batch")?.enabled).toBe(false);
  });

  it("is reachable and operable from the keyboard alone", async () => {
    withActiveRuns(api, "train-batch", 3);
    const user = userEvent.setup();
    renderRules();
    const sw = await screen.findByRole("switch", { name: "Train batch enabled" });
    sw.focus();
    await user.keyboard(" ");
    await screen.findByText(/Stop 3 current runs\?/);
    const approve = screen.getByRole("button", { name: /^Approve/ });
    approve.focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Stopped 3 runs of Train batch.")).toBeInTheDocument();
  });

  it("asks nothing when the disabled rule has no runs going", async () => {
    const user = userEvent.setup();
    renderRules();
    await disableTrainBatch(user);
    await waitFor(() => expect(sent("POST", "/rules/train-batch/disable")).toHaveLength(1));
    expect(screen.queryByText(/current run/)).not.toBeInTheDocument();
  });

  it("a refused stop keeps the question open with the failure shown", async () => {
    withActiveRuns(api, "train-batch", 2);
    api.failNext["POST /rules/train-batch/stop-runs"] = {
      status: 409,
      code: "rule_enabled",
      message: "rule train-batch is enabled",
    };
    const user = userEvent.setup();
    renderRules();
    await disableTrainBatch(user);
    await user.click(await screen.findByRole("button", { name: /^Approve/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rule train-batch is enabled");
    expect(screen.getByText(/Stop 2 current runs\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Approve/ })).toBeEnabled();
  });

  it("re-enabling the rule withdraws the question", async () => {
    withActiveRuns(api, "train-batch", 2);
    const user = userEvent.setup();
    renderRules();
    const sw = await disableTrainBatch(user);
    await screen.findByText(/Stop 2 current runs\?/);
    await user.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByText(/current runs/)).not.toBeInTheDocument();
  });
});

describe("rules API: active runs are response-only", () => {
  it("a disable answer's active_runs are split off the rule", async () => {
    withActiveRuns(api, "train-batch", 2);
    const rule = structuredClone(RULES.find((r) => r.id === "train-batch")!);
    const answer = await setRuleEnabled(rule, false);
    expect(answer.activeRunsTotal).toBe(2);
    expect(answer.activeRuns.map((r) => r.id)).toEqual(["run-active-1", "run-active-2"]);
    expect(answer.rule).not.toHaveProperty("active_runs");
    expect(answer.rule).not.toHaveProperty("active_runs_total");
    expect(answer.rule.enabled).toBe(false);
  });

  it("a save drops them too, so they are never sent back", async () => {
    const rule = structuredClone(RULES.find((r) => r.id === "train-batch")!);
    const saved = await updateRule({ ...rule, active_runs: [], active_runs_total: 0 } as typeof rule);
    expect(saved).not.toHaveProperty("active_runs");
  });
});

/** A fetch over the fake API that holds `stop-runs` requests until released. */
function holdStops(base: FakeApi) {
  const held: (() => void)[] = [];
  const inner = fetchFor(base);
  const fetchFn = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.includes("/stop-runs")) return inner(input, init);
    return new Promise<Response>((resolve) => held.push(() => resolve(inner(input, init))));
  }) as typeof fetch;
  return { fetchFn, releaseAll: () => held.splice(0).forEach((go) => go()), held };
}

describe("a stop in flight never clobbers a newer offer", () => {
  it("disabling another rule while a stop is pending keeps the new question", async () => {
    withActiveRuns(api, "train-batch", 2);
    withActiveRuns(api, "review-on-approve", 1);
    const hold = holdStops(api);
    vi.stubGlobal("fetch", hold.fetchFn);
    const user = userEvent.setup();
    renderRules();
    await disableTrainBatch(user);
    await user.click(await screen.findByRole("button", { name: /^Approve/ }));
    await waitFor(() => expect(hold.held).toHaveLength(1));

    const other = screen.getByRole("switch", { name: "Review on approve enabled" });
    await user.click(other);
    expect(await screen.findByText(/Review on approve is off\. Stop 1 current run\?/)).toBeInTheDocument();

    hold.releaseAll();
    await waitFor(() => expect(api.activeRuns["train-batch"]).toEqual([]));
    // the older request settled, but the newer question (and its controls) stay
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText(/Review on approve is off\. Stop 1 current run\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Approve: stop 1 current run of Review on approve/ })).toBeEnabled();
    expect(screen.queryByText(/Stopped 2 runs/)).not.toBeInTheDocument();
  });

  it("a failed stop does not bring back an offer withdrawn by re-enabling", async () => {
    withActiveRuns(api, "train-batch", 2);
    api.failNext["POST /rules/train-batch/stop-runs"] = {
      status: 409,
      code: "rule_enabled",
      message: "rule train-batch is enabled",
    };
    const hold = holdStops(api);
    vi.stubGlobal("fetch", hold.fetchFn);
    const user = userEvent.setup();
    renderRules();
    const sw = await disableTrainBatch(user);
    await user.click(await screen.findByRole("button", { name: /^Approve/ }));
    await waitFor(() => expect(hold.held).toHaveLength(1));
    await user.click(sw); // re-enable: the question is withdrawn
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByText(/current runs\?/)).not.toBeInTheDocument();

    hold.releaseAll();
    expect(await screen.findByRole("alert")).toHaveTextContent("rule train-batch is enabled");
    expect(screen.queryByText(/current runs\?/)).not.toBeInTheDocument();
  });
});
