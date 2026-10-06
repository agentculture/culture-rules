import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { resetWhoamiForTests } from "../hooks/useWhoami";
import { WHOAMI } from "../fixtures/rules-fixture";
import { createFakeApi, fetchFor, type FakeApi } from "../rules/fake-api";
import Variables from "./Variables";

let api: FakeApi;

function renderTab(roles?: string[]) {
  resetWhoamiForTests();
  if (roles) {
    const inner = fetchFor(api);
    vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith("/whoami")
        ? Promise.resolve(new Response(JSON.stringify({ ...WHOAMI, roles }), { status: 200 }))
        : inner(input, init),
    );
  }
  return render(
    <MemoryRouter initialEntries={["/variables"]}>
      <Variables />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  resetAgentState();
  api = createFakeApi(Date.parse("2026-10-07T12:00:00Z"));
  vi.stubGlobal("fetch", fetchFor(api));
});
afterEach(() => vi.unstubAllGlobals());

describe("Variables tab", () => {
  it("lists the variables and reports ready", async () => {
    renderTab();
    const list = await screen.findByRole("navigation", { name: "Variables" });
    expect(within(list).getAllByRole("link").map((l) => l.textContent)).toEqual([
      expect.stringContaining("trusted_authors"),
      expect.stringContaining("max_fixes"),
    ]);
    await waitFor(() => expect(getAgentState().tab).toBe("variables"));
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
  });

  it("shows the selected variable's value, version history and the rules that reference it", async () => {
    renderTab();
    expect(await screen.findByRole("heading", { level: 1, name: "Variables" })).toBeVisible();
    const history = await screen.findByRole("list", { name: "Version history" });
    await waitFor(() => expect(within(history).getAllByRole("listitem")).toHaveLength(2));
    const versions = within(history).getAllByRole("listitem");
    // newest first
    expect(versions[0]).toHaveTextContent("v2");
    expect(versions[0]).toHaveTextContent("octocat, hubot");
    expect(versions[1]).toHaveTextContent("v1");
    expect(versions[1]).toHaveTextContent("ori");
    const refs = await screen.findByRole("list", { name: "Used by rules" });
    expect(within(refs).getByRole("link", { name: /Build and publish/ })).toHaveAttribute(
      "href",
      "/rules/build-and-publish",
    );
  });

  it("lets an admin edit a list value, one item per line, and appends a version", async () => {
    const user = userEvent.setup();
    renderTab();
    const box = await screen.findByLabelText("Items, one per line");
    expect(box).toHaveValue("octocat\nhubot");
    await user.clear(box);
    await user.type(box, "octocat\nhubot\nmonalisa");
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() =>
      expect(api.calls.filter((c) => c.method === "PUT" && c.path === "/variables/trusted_authors")).toHaveLength(1),
    );
    expect(api.calls.find((c) => c.method === "PUT")?.body).toMatchObject({
      value: ["octocat", "hubot", "monalisa"],
    });
    const history = await screen.findByRole("list", { name: "Version history" });
    await waitFor(() => expect(within(history).getAllByRole("listitem")[0]).toHaveTextContent("v3"));
  });

  it("keeps a numeric list numeric and a scalar a scalar", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.click(await screen.findByRole("link", { name: /max_fixes/ }));
    const box = await screen.findByLabelText("Value");
    expect(box).toHaveValue("3");
    await user.clear(box);
    await user.type(box, "5");
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toMatchObject({ value: 5 }));
  });

  it("is read only for anyone who is not an admin", async () => {
    renderTab(["viewer", "editor"]);
    expect(await screen.findByText(/Only admins can change variables/)).toBeVisible();
    await screen.findByRole("list", { name: "Version history" });
    expect(screen.queryByRole("button", { name: "Save new version" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Items, one per line")).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Current items" })).toHaveTextContent("octocat");
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("says in plain words when a save is refused", async () => {
    const user = userEvent.setup();
    renderTab();
    api.failNext["PUT /variables/trusted_authors"] = { status: 403, code: "forbidden_role", message: "raw" };
    await user.type(await screen.findByLabelText("Items, one per line"), "\nx");
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your role is not allowed");
    expect(alert).not.toHaveTextContent("raw");
  });
});
