import { render, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { getWorkflowDef } from "../api/workflows";
import { resetAgentState } from "../agent-state/store";
import { createFakeApi, fetchFor, type FakeApi } from "../rules/fake-api";
import { DOMMatrixStub, MeasuringResizeObserver } from "../test/reactFlow";
import { VIEW_MODE_KEY } from "../workflows/views/mode";
import { REVIEW_PR } from "../workflows/fixture";

/**
 * t16: a save sends the revision it read (`expected_revision`); two tabs open on revision 3, the
 * first saves (-> 4) and the second gets "This workflow changed since you opened it" with a
 * Reload, keeping its draft until Reload is clicked.
 */
const CHANGED = "This workflow changed since you opened it";

function seeded(): FakeApi {
  const api = createFakeApi();
  api.workflows = [{ ...structuredClone(REVIEW_PR), revision: 3 }];
  return api;
}

/** One browser tab: the Workflows page on review-pr. */
function openTab() {
  const view = render(
    <MemoryRouter initialEntries={["/workflows?id=review-pr"]}>
      <Routes>
        <Route path="/workflows" element={<Workflows />} />
      </Routes>
    </MemoryRouter>,
  );
  return within(view.container);
}

async function rename(tab: ReturnType<typeof openTab>, name: string) {
  const user = userEvent.setup();
  await user.click(await tab.findByRole("button", { name: "Rename workflow" }));
  const field = within(tab.getByRole("form", { name: "Rename workflow" })).getByRole("textbox", { name: "Name" });
  await user.clear(field);
  await user.type(field, `${name}{Enter}`);
  return user;
}

beforeEach(() => {
  localStorage.setItem(VIEW_MODE_KEY, "detailed");
  resetAgentState();
  vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
});
afterEach(() => {
  localStorage.removeItem(VIEW_MODE_KEY);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Workflows page: saves carry the revision read", () => {
  it("sends expected_revision, shows the new revision after a save, and the next save sends that", async () => {
    const api = seeded();
    const fetchMock = vi.fn(fetchFor(api));
    vi.stubGlobal("fetch", fetchMock);
    const tab = openTab();
    const user = await rename(tab, "First name");
    await user.click(tab.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.workflows[0].revision).toBe(4));
    expect(api.workflows[0].name).toBe("First name");
    await rename(tab, "Second name");
    await user.click(tab.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.workflows[0].revision).toBe(5));
    const puts = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(puts.map(([, init]) => JSON.parse(String(init?.body)).expected_revision)).toEqual([3, 4]);
    expect(JSON.parse(String(puts[0][1]?.body))).not.toHaveProperty("revision");
  });

  it("two tabs on revision 3: the first saves, the second gets the conflict, keeps its draft, Reload replaces it", async () => {
    const api = seeded();
    vi.stubGlobal("fetch", fetchMock(api));
    const one = openTab();
    const two = openTab();
    const userOne = await rename(one, "Tab one name");
    const userTwo = await rename(two, "Tab two name");
    await userOne.click(one.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.workflows[0].revision).toBe(4));

    await userTwo.click(two.getByRole("button", { name: "Save" }));
    const alert = await two.findByText(CHANGED, { exact: false });
    expect(alert).toBeInTheDocument();
    expect(api.workflows[0]).toMatchObject({ name: "Tab one name", revision: 4 });
    // The draft stays: the unsaved name is still on the page, and Save is still offered.
    expect(two.getByRole("heading", { level: 1, name: "Tab two name" })).toBeInTheDocument();

    await userTwo.click(two.getByRole("button", { name: "Reload" }));
    await two.findByRole("heading", { level: 1, name: "Tab one name" });
    expect(two.queryByText(CHANGED, { exact: false })).toBeNull();
    // Reloaded at revision 4: the next save goes through.
    await rename(two, "Tab two again");
    await userTwo.click(two.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.workflows[0]).toMatchObject({ name: "Tab two again", revision: 5 }));
    expect((await getWorkflowDef("review-pr")).revision).toBe(5);
  });
});

const fetchMock = (api: FakeApi) => fetchFor(api);
