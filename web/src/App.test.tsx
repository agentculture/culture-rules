import { render, screen, within, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { resetAgentState, getAgentState } from "./agent-state/store";
import { resetWhoamiForTests } from "./hooks/useWhoami";
import { defaultRoutes, mockFetch } from "./test/mockApi";

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

  it("has exactly four top-level tabs, in order", () => {
    renderAt("/rules");
    const nav = screen.getByRole("navigation", { name: "Primary" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "Rules",
      "Workflows",
      "Actors",
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
    expect(within(nav).getByRole("link", { name: "Rules" })).not.toHaveAttribute("aria-current");
  });

  it.each(["/runs", "/history", "/ledger", "/inbox", "/"])(
    "has no top-level %s route: it lands on Rules",
    async (path) => {
      renderAt(path);
      const nav = screen.getByRole("navigation", { name: "Primary" });
      await waitFor(() =>
        expect(within(nav).getByRole("link", { name: "Rules" })).toHaveAttribute(
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
    renderAt("/rules");
    expect(
      await screen.findByRole("button", { name: "Signed in as ci-bot (service token)" }),
    ).toHaveTextContent("C");
  });

  it("shows who is signed in, from GET /whoami", async () => {
    renderAt("/rules");
    expect(
      await screen.findByRole("button", { name: "Signed in as ori (SSO)" }),
    ).toHaveTextContent("O");
  });
});
