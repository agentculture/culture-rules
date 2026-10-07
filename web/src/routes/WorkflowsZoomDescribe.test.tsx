import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { MACHINES, WHOAMI } from "../fixtures/rules-fixture";
import { mockFetch, type Routes as ApiRoutes } from "../test/mockApi";
import { DOMMatrixStub, MeasuringResizeObserver, useMeasuredLayout } from "../test/reactFlow";
import { ACTORS, REVIEW_PR, WORKFLOW_DOCS, WORKFLOW_RULES } from "../workflows/fixture";
import type { Description } from "../api/types";

/** What `GET /workflows/review-pr/describe` answers in these tests. */
const DESCRIBED: Description = {
  id: "review-pr",
  kind: "workflow",
  lines: ["1 fetch — code on spark", "2 loop — retry up to 3×:", "  2.1 agent — reviewer (agent)"],
  entries: [
    { label: "1", text: "code on spark", depth: 0, step: "fetch" },
    { label: "2", text: "retry up to 3×:", depth: 0, step: "loop" },
    { label: "2.1", text: "reviewer (agent)", depth: 1, step: "agent" },
  ],
};

function routes(overrides: ApiRoutes = {}): ApiRoutes {
  return {
    "/api/whoami": { body: WHOAMI },
    "/api/rules": { body: { items: WORKFLOW_RULES } },
    "/api/machines": { body: { items: MACHINES } },
    "/api/workflows": { body: { items: WORKFLOW_DOCS } },
    "/api/workflows/review-pr": { body: REVIEW_PR },
    "/api/actors": { body: { items: ACTORS } },
    "/api/runs": { body: { items: [] } },
    "/api/workflows/review-pr/describe": { body: DESCRIBED },
    ...overrides,
  };
}

function renderWorkflows() {
  return render(
    <MemoryRouter initialEntries={["/workflows?id=review-pr"]}>
      <Routes>
        <Route path="/workflows" element={<Workflows />} />
      </Routes>
    </MemoryRouter>,
  );
}

useMeasuredLayout();

async function loaded() {
  await screen.findByRole("heading", { level: 1, name: "Review PR" });
  await waitFor(() => expect(getAgentState().status).toBe("ready"));
}

const level = () => screen.getByTestId("zoom-level");
const scale = () =>
  document.querySelector<HTMLElement>(".wf-canvas .react-flow__viewport")?.style.transform ?? "";

describe("Workflows: zoom and the plain description (d19)", () => {
  beforeEach(() => {
    resetAgentState();
    vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
    mockFetch(routes());
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("zoom out / zoom in / fit buttons are labelled, change the zoom and stop at the bounds", async () => {
    renderWorkflows();
    await loaded();
    const zoom = screen.getByRole("group", { name: "Zoom" });
    const zoomIn = within(zoom).getByRole("button", { name: "Zoom in" });
    const zoomOut = within(zoom).getByRole("button", { name: "Zoom out" });
    expect(level()).toHaveTextContent("100%");
    await userEvent.click(zoomIn);
    expect(level()).toHaveTextContent("125%");
    await waitFor(() => expect(scale()).toContain("scale(1.25)"));
    await userEvent.click(zoomOut);
    await userEvent.click(zoomOut);
    expect(level()).toHaveTextContent("80%");
    for (let i = 0; i < 10; i++) fireEvent.click(zoomIn);
    expect(level()).toHaveTextContent("200%");
    expect(zoomIn).toBeDisabled();
    await userEvent.click(within(zoom).getByRole("button", { name: "Fit to width" }));
    expect(level()).toHaveTextContent("100%"); // the graph fits: fit is the reset
    for (let i = 0; i < 10; i++) fireEvent.click(zoomOut);
    expect(level()).toHaveTextContent("25%");
    expect(zoomOut).toBeDisabled();
  });

  it("+ / - / 0 zoom while focus is in the canvas, but not while typing", async () => {
    renderWorkflows();
    await loaded();
    const node = screen.getByRole("group", { name: "Review" });
    const flowNode = node.closest(".react-flow__node") as HTMLElement;
    fireEvent.keyDown(flowNode, { key: "+" });
    expect(level()).toHaveTextContent("125%");
    fireEvent.keyDown(flowNode, { key: "-" });
    fireEvent.keyDown(flowNode, { key: "-" });
    expect(level()).toHaveTextContent("80%");
    fireEvent.keyDown(flowNode, { key: "0" });
    expect(level()).toHaveTextContent("100%");
    fireEvent.keyDown(flowNode, { key: "+", ctrlKey: true }); // the browser's page zoom
    expect(level()).toHaveTextContent("100%");
    const canvas = screen.getByRole("region", { name: "Workflow canvas" });
    const field = document.createElement("input");
    canvas.appendChild(field);
    fireEvent.keyDown(field, { key: "+" });
    expect(level()).toHaveTextContent("100%");
  });

  it("cmd + wheel zooms; a plain wheel is left to scroll the page", async () => {
    renderWorkflows();
    await loaded();
    const canvas = screen.getByRole("region", { name: "Workflow canvas" });
    const plain = fireEvent.wheel(canvas, { deltaY: -100 });
    expect(plain).toBe(true); // not prevented: the page scrolls
    expect(level()).toHaveTextContent("100%");
    fireEvent.wheel(canvas, { deltaY: -100, metaKey: true });
    expect(level()).toHaveTextContent("125%");
    fireEvent.wheel(canvas, { deltaY: 100, metaKey: true });
    expect(level()).toHaveTextContent("100%");
  });

  it("the canvas grows and shrinks with the zoom", async () => {
    renderWorkflows();
    await loaded();
    const canvas = screen.getByRole("region", { name: "Workflow canvas" });
    const zoom = screen.getByRole("group", { name: "Zoom" });
    for (let i = 0; i < 3; i++) await userEvent.click(within(zoom).getByRole("button", { name: "Zoom in" }));
    const tall = parseFloat(canvas.style.height);
    await userEvent.click(within(zoom).getByRole("button", { name: "Fit to width" }));
    expect(parseFloat(canvas.style.height)).toBeLessThan(tall);
  });

  it("shows the workflow 'In words' from GET /workflows/{id}/describe, nested by depth", async () => {
    renderWorkflows();
    await loaded();
    const words = await screen.findByRole("region", { name: "In words" });
    const lines = within(words).getAllByRole("listitem");
    expect(lines.map((l) => l.textContent)).toEqual([
      "1fetchcode on spark",
      "2loopretry up to 3×:",
      "2.1agentreviewer (agent)",
    ]);
    expect(lines[2].style.getPropertyValue("--describe-depth")).toBe("1");
    expect(within(words).queryByText(/the saved version/)).toBeNull();
  });

  it("marks the description as the saved version while the draft has unsaved edits", async () => {
    renderWorkflows();
    await loaded();
    await screen.findByRole("region", { name: "In words" });
    await userEvent.click(screen.getByRole("switch", { name: "Review enabled" }));
    expect(await screen.findByText(/the saved version/)).toBeInTheDocument();
  });

  it("shows no description when the describe call fails", async () => {
    vi.unstubAllGlobals();
    vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
    vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
    mockFetch(routes({ "/api/workflows/review-pr/describe": { status: 500, body: {} } }));
    renderWorkflows();
    await loaded();
    expect(screen.queryByRole("region", { name: "In words" })).toBeNull();
  });
});
