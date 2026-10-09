import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { RuleRedirect } from "./LegacyRoutes";

/** `/rules/:ruleId` while it looks the rule up (t8 review, t9). */
describe("RuleRedirect", () => {
  beforeEach(() => {
    resetAgentState();
    // The rules never answer: the redirect stays on its lookup.
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => {})));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reports the Workflows tab, not ready, while it finds where the rule lives", async () => {
    render(
      <MemoryRouter initialEntries={["/rules/build-and-publish"]}>
        <Routes>
          <Route path="/rules/:ruleId?" element={<RuleRedirect />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("status")).toHaveTextContent("Finding where this rule lives now");
    await waitFor(() => expect(getAgentState().tab).toBe("workflows"));
    expect(getAgentState()).toMatchObject({ status: "loading", view_ready: false, errors: [] });
  });
});
