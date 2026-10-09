import { render, screen, within, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { resetAgentState, getAgentState } from "./agent-state/store";
import { resetWhoamiForTests } from "./hooks/useWhoami";
import { defaultRoutes, mockFetch } from "./test/mockApi";
import { SELECTED_RULE_ID } from "./fixtures/rules-fixture";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App shell", () => {
  beforeEach(() => {
    resetAgentState();
    resetWhoamiForTests();
    mockFetch(defaultRoutes());
  });
  afterEach(() => vi.unstubAllGlobals());

  it("has exactly four top-level tabs, in order: rules are folded into Workflows (c16)", () => {
    renderAt("/workflows");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "Workflows",
      "Actors",
      "Variables",
      "Statistics",
    ]);
  });

  it("marks the current tab with aria-current=page", () => {
    renderAt("/workflows");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Workflows" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(nav).getByRole("link", { name: "Actors" })).not.toHaveAttribute("aria-current");
    expect(within(nav).queryByRole("link", { name: "Rules" })).toBeNull();
  });

  it.each(["/runs", "/history", "/ledger", "/inbox", "/"])(
    "has no top-level %s route: it lands on Workflows",
    async (path) => {
      renderAt(path);
      const nav = screen.getByRole("navigation", { name: "Primary" });
      await waitFor(() =>
        expect(within(nav).getByRole("link", { name: "Workflows" })).toHaveAttribute(
          "aria-current",
          "page",
        ),
      );
    },
  );

  it("renders exactly one #agent-state node and reports the tab", async () => {
    const { container } = renderAt("/actors");
    expect(container.ownerDocument.querySelectorAll("#agent-state")).toHaveLength(1);
    await waitFor(() => expect(getAgentState().tab).toBe("actors"));
  });

  it("names the credential kind on the avatar for a service identity", async () => {
    mockFetch({
      ...defaultRoutes(),
      "/api/whoami": { body: { identity: "ci-bot", kind: "service", roles: ["editor"] } },
    });
    renderAt("/workflows");
    expect(
      await screen.findByRole("button", { name: "Signed in as ci-bot (service token)" }),
    ).toHaveTextContent("C");
  });

  it("shows who is signed in, from GET /whoami", async () => {
    renderAt("/workflows");
    expect(
      await screen.findByRole("button", { name: "Signed in as ori (SSO)" }),
    ).toHaveTextContent("O");
  });

  it("an old /rules/:ruleId link redirects to its workflow, at that entry point", async () => {
    let where = "";
    function Where() {
      const loc = useLocation();
      where = `${loc.pathname}${loc.search}`;
      return null;
    }
    render(
      <MemoryRouter initialEntries={[`/rules/${SELECTED_RULE_ID}`]}>
        <App />
        <Where />
      </MemoryRouter>,
    );
    await waitFor(() => expect(where).toBe(`/workflows?id=build-image&entry=${SELECTED_RULE_ID}`));
  });

  it("an old link to a rule with no workflow lands on the list with that rule chosen (D7)", async () => {
    let where = "";
    function Where() {
      const loc = useLocation();
      where = `${loc.pathname}${loc.search}`;
      return null;
    }
    render(
      <MemoryRouter initialEntries={["/rules/review-on-approve"]}>
        <App />
        <Where />
      </MemoryRouter>,
    );
    await waitFor(() => expect(where).toBe("/workflows?entry=review-on-approve"));
  });

  it("an unknown rule id lands on Workflows with a not-found notice", async () => {
    renderAt("/rules/no-such-rule");
    expect(await screen.findByText(/No rule “no-such-rule” any more/)).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: "Workflows" })).toHaveAttribute("aria-current", "page");
  });

  it("bare /rules lands on Workflows", async () => {
    renderAt("/rules");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    await waitFor(() =>
      expect(within(nav).getByRole("link", { name: "Workflows" })).toHaveAttribute("aria-current", "page"),
    );
  });

  it("agent-state reports tab workflows with the entry point, and keeps the deprecated rules alias (c33)", async () => {
    renderAt(`/workflows?id=build-image&entry=${SELECTED_RULE_ID}`);
    await waitFor(() => expect(getAgentState().tab).toBe("workflows"));
    await waitFor(() =>
      expect(getAgentState().workflows).toMatchObject({ selected: "build-image", entry: SELECTED_RULE_ID }),
    );
    expect(getAgentState().workflows?.entries).toContain(SELECTED_RULE_ID);
    expect(getAgentState().rules).toMatchObject({ selected: SELECTED_RULE_ID });
    expect(getAgentState().rules?.stages).toContain("workflow");
    expect(getAgentState().rules?.count).toBeGreaterThan(0);
  });
});
