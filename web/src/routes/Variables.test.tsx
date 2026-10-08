import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { resetWhoamiForTests } from "../hooks/useWhoami";
import { WHOAMI } from "../fixtures/rules-fixture";
import { createFakeApi, fetchFor, type FakeApi } from "../rules/fake-api";
import Variables, { rowsOf, valueOfRows } from "./Variables";

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

  it("lets an admin edit a list one item per row and appends a version", async () => {
    const user = userEvent.setup();
    renderTab();
    expect(await screen.findByLabelText("Item 1")).toHaveValue("octocat");
    expect(screen.getByLabelText("Item 2")).toHaveValue("hubot");
    await user.click(screen.getByRole("button", { name: "Add item" }));
    await user.type(screen.getByLabelText("Item 3"), "monalisa");
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

  it("removes one item and leaves the rest", async () => {
    const user = userEvent.setup();
    renderTab();
    await screen.findByLabelText("Item 1");
    await user.click(screen.getByRole("button", { name: "Remove item 1" }));
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")?.body).toMatchObject({ value: ["hubot"] }));
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
    expect(screen.queryByLabelText("Item 1")).not.toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Current items" })).toHaveTextContent("octocat");
    expect(api.calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("says in plain words when a save is refused", async () => {
    const user = userEvent.setup();
    renderTab();
    api.failNext["PUT /variables/trusted_authors"] = { status: 403, code: "forbidden_role", message: "raw" };
    await user.type(await screen.findByLabelText("Item 2"), "x");
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your role is not allowed");
    expect(alert).not.toHaveTextContent("raw");
  });
});

const NASTY = [" alice ", "a\nb", 1, null, "", true] as const;

describe("lossless editing", () => {
  // characterization (the Sonar S3776 split of valueOfRows): each row outcome, pinned
  it("drops a blank new row, re-types edited rows by their stored item, keeps the rest as text", () => {
    expect(valueOfRows([1], [...rowsOf([1]), { id: 99, text: "" }])).toEqual({ value: [1], invalid: [] });
    const withNull = rowsOf([1, null]);
    withNull[1] = { ...withNull[1], text: "5" };
    // empty items (null, "") do not break a uniform list: an edited null in a number list is a number
    expect(valueOfRows([1, null], withNull)).toEqual({ value: [1, 5], invalid: [] });
    const flag = rowsOf([true]);
    flag[0] = { ...flag[0], text: "false" };
    expect(valueOfRows([true], flag)).toEqual({ value: [false], invalid: [] });
    const word = rowsOf(["a"]);
    word[0] = { ...word[0], text: "true" };
    expect(valueOfRows(["a"], word)).toEqual({ value: ["true"], invalid: [] });
    const nothing = rowsOf([null, ""]);
    nothing[0] = { ...nothing[0], text: "x" };
    expect(valueOfRows([null, ""], [...nothing, { id: 98, text: "7" }])).toEqual({
      value: ["x", "", "7"],
      invalid: [],
    });
    expect(valueOfRows([], [{ id: 97, text: "false" }])).toEqual({ value: ["false"], invalid: [] });
    const bad = [...rowsOf([2]), { id: 96, text: "x" }, { id: 95, text: "" }, { id: 94, text: "4" }];
    expect(valueOfRows([2], bad)).toEqual({ value: [2, 4], invalid: [1] });
  });

  it("an edited empty item takes the list's type: a number, a boolean, or invalid", () => {
    const nums = rowsOf([1, null, "", 2]);
    nums[1] = { ...nums[1], text: "5" };
    nums[2] = { ...nums[2], text: "6" };
    expect(valueOfRows([1, null, "", 2], nums)).toEqual({ value: [1, 5, 6, 2], invalid: [] });
    const notNum = rowsOf([1, null]);
    notNum[1] = { ...notNum[1], text: "x" };
    expect(valueOfRows([1, null], notNum)).toEqual({ value: [1], invalid: [1] });
    const flags = rowsOf([true, null]);
    flags[1] = { ...flags[1], text: "false" };
    expect(valueOfRows([true, null], flags)).toEqual({ value: [true, false], invalid: [] });
    const newRow = [...rowsOf([1, null]), { id: 93, text: "8" }];
    expect(valueOfRows([1, null], newRow)).toEqual({ value: [1, null, 8], invalid: [] });
    // a mixed list stays text
    const mixed = rowsOf([1, "a", null]);
    mixed[2] = { ...mixed[2], text: "5" };
    expect(valueOfRows([1, "a", null], mixed)).toEqual({ value: [1, "a", "5"], invalid: [] });
  });

  it("a list saved without edits is unchanged, whatever its items", () => {
    const list = [...NASTY];
    expect(valueOfRows(list, rowsOf(list)).value).toEqual(list);
  });

  it("editing one item leaves the others exact", () => {
    const list = [...NASTY];
    const rows = rowsOf(list);
    rows[0] = { ...rows[0], text: " alice2 " };
    expect(valueOfRows(list, rows).value).toEqual([" alice2 ", "a\nb", 1, null, "", true]);
  });

  it("an edited number stays a number, and a bad number is flagged", () => {
    const list = [1, 2];
    const rows = rowsOf(list);
    rows[1] = { ...rows[1], text: "7" };
    expect(valueOfRows(list, rows).value).toEqual([1, 7]);
    rows[1] = { ...rows[1], text: "x" };
    expect(valueOfRows(list, rows).invalid).toEqual([1]);
  });

  it("a new row is typed like a uniform numeric list, else a string", () => {
    const rows = [...rowsOf([1, 2]), { id: 99, text: "3" }];
    expect(valueOfRows([1, 2], rows).value).toEqual([1, 2, 3]);
    expect(valueOfRows(["a"], [...rowsOf(["a"]), { id: 99, text: "3" }]).value).toEqual(["a", "3"]);
  });

  it("a new row in a uniform boolean list is a boolean; other text is refused", () => {
    const added = (text: string) => [...rowsOf([true]), { id: 99, text }];
    expect(valueOfRows([true], added("false"))).toEqual({ value: [true, false], invalid: [] });
    expect(valueOfRows([true], added("true")).value).toEqual([true, true]);
    expect(valueOfRows([true], added("maybe")).invalid).toEqual([1]);
    // a mixed list keeps new rows as text
    expect(valueOfRows([true, "x"], [...rowsOf([true, "x"]), { id: 99, text: "false" }]).value).toEqual([
      true,
      "x",
      "false",
    ]);
  });

  it("the editor shows untouched odd strings exactly and saves nothing until something changes", async () => {
    const user = userEvent.setup();
    api.variableVersions.push({
      id: "odd",
      name: "odd",
      value: [" alice ", "a\nb", 1, null, ""],
      version: 1,
      updated_by: "ori",
      updated_at: "2026-10-07T00:00:00Z",
      description: null,
    });
    renderTab();
    await user.click(await screen.findByRole("link", { name: /odd/ }));
    expect(await screen.findByLabelText("Item 1")).toHaveValue(" alice ");
    expect(screen.getByRole("button", { name: "Save new version" })).toBeDisabled();
    await user.type(screen.getByLabelText("Item 5"), "z");
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")).toBeTruthy());
    expect(api.calls.find((c) => c.method === "PUT")?.body).toMatchObject({
      value: [" alice ", "a\nb", 1, null, "z"],
    });
  });

  it("after switching variables the old one's history and rules are not shown while the new load is pending", async () => {
    const user = userEvent.setup();
    const inner = fetchFor(api);
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).includes("/variables/max_fixes/")) await gate;
      return inner(input, init);
    });
    resetWhoamiForTests();
    render(
      <MemoryRouter initialEntries={["/variables"]}>
        <Variables />
      </MemoryRouter>,
    );
    const history = await screen.findByRole("list", { name: "Version history" });
    await waitFor(() => expect(within(history).getAllByRole("listitem")).toHaveLength(2));
    await user.click(screen.getByRole("link", { name: /max_fixes/ }));
    await screen.findByRole("heading", { level: 2, name: "max_fixes" });
    expect(screen.queryByRole("list", { name: "Version history" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Build and publish/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Loading/)).toBeVisible();
    release();
    await waitFor(() => expect(screen.queryByText(/Loading/)).not.toBeInTheDocument());
  });

  it("an edit keeps the description (the backend stores an omitted one as null)", async () => {
    const user = userEvent.setup();
    renderTab();
    await user.type(await screen.findByLabelText("Item 2"), "x");
    await user.click(screen.getByRole("button", { name: "Save new version" }));
    await waitFor(() => expect(api.calls.find((c) => c.method === "PUT")).toBeTruthy());
    expect(api.calls.find((c) => c.method === "PUT")?.body).toMatchObject({
      description: "PR authors the fixer may act on",
    });
    expect(api.variableVersions.at(-1)?.description).toBe("PR authors the fixer may act on");
  });
});
