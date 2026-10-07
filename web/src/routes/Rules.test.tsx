import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Rules from "./Rules";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { SELECTED_RULE_ID } from "../fixtures/rules-fixture";
import * as client from "../api/client";
import { defaultRoutes, mockFetch } from "../test/mockApi";

function renderRules(path = `/rules/${SELECTED_RULE_ID}`) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/rules/:ruleId?" element={<Rules />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Rules board (Chosen — Rules)", () => {
  beforeEach(() => {
    resetAgentState();
    mockFetch(defaultRoutes(Date.parse("2026-10-03T12:00:00Z")));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("the rule's (i), in the head and on its list row, opens GET /rules/{id}/describe (d19)", async () => {
    vi.unstubAllGlobals();
    const lines = ["When push", "If verdict = approve", "and repo ∉ vars.excluded", "Then publish"];
    mockFetch({
      ...defaultRoutes(Date.parse("2026-10-03T12:00:00Z")),
      [`/api/rules/${SELECTED_RULE_ID}/describe`]: {
        body: { id: SELECTED_RULE_ID, kind: "rule", lines, entries: [] },
      },
    });
    renderRules();
    await screen.findByRole("heading", { level: 1, name: "Build and publish" });
    const buttons = screen.getAllByRole("button", { name: "About Build and publish" });
    expect(buttons).toHaveLength(2); // the list row and the rule's head
    const list = screen.getByRole("navigation", { name: "Rules" });
    const rowButton = within(list).getByRole("button", { name: "About Build and publish" });
    await userEvent.click(rowButton);
    const panel = within(list).getByRole("dialog", { name: "About Build and publish" });
    expect((await within(panel).findByTestId("about-lines")).textContent).toBe(lines.join("\n"));
    await userEvent.keyboard("{Escape}");
    expect(rowButton).toHaveFocus();
    const headButton = buttons.find((b) => !list.contains(b))!;
    await userEvent.click(headButton);
    expect(await screen.findAllByTestId("about-lines")).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "In words" })).toBeNull();
  });

  it("lists every rule with an enable switch, the first affordance a 'New rule' button", async () => {
    renderRules();
    const list = await screen.findByRole("navigation", { name: "Rules" });
    expect(within(list).getByRole("button", { name: "New rule" })).toBeInTheDocument();
    await waitFor(() => expect(within(list).getAllByRole("switch")).toHaveLength(5));
    expect(within(list).getByRole("switch", { name: "Clean caches enabled" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
    expect(within(list).getByRole("link", { name: "Build and publish" })).toHaveAttribute(
      "aria-current",
      "true",
    );
  });

  it("draws the selected rule as Trigger → Condition → Workflow → Action stages", async () => {
    renderRules();
    expect(await screen.findByRole("heading", { level: 1, name: "Build and publish" })).toBeInTheDocument();
    const stages = await screen.findAllByTestId(/^stage-/);
    expect(stages.map((s) => s.getAttribute("data-testid"))).toEqual([
      "stage-trigger",
      "stage-condition",
      "stage-workflow",
      "stage-action",
    ]);
    expect(screen.getByTestId("stage-trigger")).toHaveTextContent("Push to main");
    expect(screen.getByTestId("stage-condition")).toHaveTextContent("verdict is approve");
    expect(screen.getByTestId("stage-workflow")).toHaveTextContent("Build image");
    expect(screen.getByTestId("stage-workflow")).toHaveTextContent("sha → commit");
    expect(screen.getByTestId("stage-action")).toHaveTextContent("Publish");
    expect(screen.getByTestId("stage-action")).toHaveTextContent("image → tag");
    expect(screen.getByRole("button", { name: "Add stage" })).toBeInTheDocument();
  });

  it("shows a relationship as a dashed card, not a stage", async () => {
    renderRules();
    const rel = await screen.findByTestId("relationship");
    expect(rel).toHaveTextContent("must run after Review on approve");
    // The upstream output the condition reads rides on the relationship.
    expect(rel).toHaveTextContent("verdict");
  });

  it("shows placement with the machine's color", async () => {
    renderRules();
    const chip = await screen.findByRole("button", { name: /on thor/ });
    expect(chip).toHaveAttribute("data-machine-slot", "1");
  });

  it("lists the last runs contextually, with failure as icon+word", async () => {
    renderRules();
    const aside = await screen.findByRole("complementary", { name: "Last runs" });
    await waitFor(() => expect(within(aside).getAllByRole("listitem")).toHaveLength(4));
    expect(within(aside).getByText(/failed/)).toBeInTheDocument();
  });

  it("reports ready in agent-state with the stages it drew", async () => {
    renderRules();
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    expect(getAgentState().rules).toMatchObject({
      count: 5,
      selected: SELECTED_RULE_ID,
      stages: ["trigger", "condition", "workflow", "action"],
    });
    expect(getAgentState().errors).toEqual([]);
  });

  it("selects the first rule when none is named", async () => {
    renderRules("/rules");
    expect(await screen.findByRole("heading", { level: 1, name: "Review on approve" })).toBeInTheDocument();
  });

  it("names a failure that has no string form and still reports ready", async () => {
    mockFetch(defaultRoutes());
    // A rejection reason with no string form: String(reason) throws while the load is applied.
    vi.spyOn(client, "listMachines").mockRejectedValue(Object.create(null));
    renderRules();
    expect(await screen.findByRole("alert")).toHaveTextContent("unexpected error");
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
  });

  it("names a load failure and still reports ready", async () => {
    mockFetch({ ...defaultRoutes(), "/api/rules": { status: 503, body: { error: { code: "store_down", message: "store unreachable", errors: [] } } } });
    renderRules();
    expect(await screen.findByRole("alert")).toHaveTextContent("store unreachable");
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    expect(getAgentState().errors).toEqual(["store unreachable"]);
  });
});
