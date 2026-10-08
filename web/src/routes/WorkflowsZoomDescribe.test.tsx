import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

describe("Workflows: zoom and the (i) description (d19)", () => {
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

  it("Fit uses the canvas's current width after a resize (not the width at mount)", async () => {
    // A ResizeObserver whose callbacks the test can fire again after changing a width.
    const observers: { cb: ResizeObserverCallback; targets: Element[] }[] = [];
    class RecordingObserver extends MeasuringResizeObserver {
      private readonly entry: { cb: ResizeObserverCallback; targets: Element[] };
      constructor(cb: ResizeObserverCallback) {
        super(cb);
        this.entry = { cb, targets: [] };
        observers.push(this.entry);
      }
      observe(target: Element) {
        this.entry.targets.push(target);
        super.observe(target);
      }
    }
    vi.stubGlobal("ResizeObserver", RecordingObserver);
    let canvasWidth = 4000;
    const widthSpy = vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockImplementation(function (
      this: HTMLElement,
    ) {
      return this.classList.contains("wf-canvas") ? canvasWidth : 0;
    });
    renderWorkflows();
    await loaded();
    const canvas = screen.getByRole("region", { name: "Workflow canvas" });
    const fit = screen.getByRole("button", { name: "Fit to width" });
    await userEvent.click(fit);
    expect(level()).toHaveTextContent("100%"); // wide canvas: the graph fits at 100%
    // Narrow the window: the canvas shrinks and its observer reports it.
    canvasWidth = 700;
    act(() => {
      for (const o of observers) {
        if (o.targets.includes(canvas)) o.cb([{ target: canvas } as unknown as ResizeObserverEntry], {} as ResizeObserver);
      }
    });
    await userEvent.click(fit);
    const zoom = parseInt(level().textContent!.replace(/\D/g, ""), 10) / 100;
    expect(zoom).toBeLessThan(1);
    // The fitted graph (plus its 20px margins) is no wider than the narrowed canvas.
    const graph = document.querySelector<HTMLElement>(".wf-canvas__graph")!;
    expect(parseFloat(graph.style.width)).toBeLessThanOrEqual(700);
    widthSpy.mockRestore();
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

  it("the head's (i) opens the workflow's description from GET /workflows/{id}/describe", async () => {
    renderWorkflows();
    await loaded();
    const about = within(document.querySelector(".wf-head") as HTMLElement).getByRole("button", {
      name: "About Review PR",
    });
    await userEvent.click(about);
    const panel = screen.getByRole("dialog", { name: "About Review PR" });
    expect((await within(panel).findByTestId("about-lines")).textContent).toBe(DESCRIBED.lines.join("\n"));
    expect(within(panel).queryByText("The saved version.")).toBeNull();
    // the inline block is gone: the description lives behind the button
    expect(screen.queryByRole("region", { name: "In words" })).toBeNull();
  });

  it("with unsaved edits the head's panel says it is the saved version", async () => {
    renderWorkflows();
    await loaded();
    const toggle = screen.getByRole("switch", { name: "Review enabled" });
    const before = toggle.getAttribute("aria-checked");
    await userEvent.click(toggle);
    // the draft is dirty only once the toggle's state has landed; under a loaded parallel run
    // opening the panel first raced it
    await waitFor(() => expect(toggle.getAttribute("aria-checked")).not.toBe(before));
    const head = document.querySelector(".wf-head") as HTMLElement;
    await userEvent.click(within(head).getByRole("button", { name: "About Review PR" }));
    expect(await screen.findByText("The saved version.", undefined, { timeout: 5000 })).toBeInTheDocument();
  });

  it("the list row's (i) opens the same description without leaving the open workflow", async () => {
    renderWorkflows();
    await loaded();
    const list = screen.getByRole("navigation", { name: "Workflows" });
    await userEvent.click(within(list).getByRole("button", { name: "About Review PR" }));
    const panel = within(list).getByRole("dialog", { name: "About Review PR" });
    expect((await within(panel).findByTestId("about-lines")).textContent).toBe(DESCRIBED.lines.join("\n"));
    expect(screen.getByRole("heading", { level: 1, name: "Review PR" })).toBeInTheDocument();
  });
});
