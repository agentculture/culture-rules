import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { WorkflowDef } from "../api/workflows";
import { MACHINES } from "../fixtures/rules-fixture";
import { DOMMatrixStub, MeasuringResizeObserver, useMeasuredLayout } from "../test/reactFlow";
import WorkflowCanvas, { type CanvasProps } from "./Canvas";
import { bundleId } from "./model";

useMeasuredLayout();

/** Fetch feeds Review three wires; Review feeds Outputs one. Both steps on spark. */
const THREE: WorkflowDef = {
  id: "three",
  name: "Three",
  version: 1,
  inputs: [{ name: "pr", type: "integer" }],
  steps: [
    {
      id: "fetch",
      name: "Fetch",
      kind: "code",
      placement: { machine: "spark" },
      inputs: [{ name: "pr", type: "integer" }],
      outputs: [
        { name: "a", type: "string" },
        { name: "b", type: "string" },
        { name: "c", type: "string" },
      ],
    },
    {
      id: "review",
      name: "Review",
      kind: "code",
      placement: { machine: "spark" },
      inputs: [
        { name: "a", type: "string" },
        { name: "b", type: "string" },
        { name: "c", type: "string" },
      ],
      outputs: [{ name: "verdict", type: "string" }],
    },
  ],
  edges: [
    { source: "inputs", source_port: "pr", target: "fetch", target_port: "pr" },
    { source: "fetch", source_port: "a", target: "review", target_port: "a" },
    { source: "fetch", source_port: "b", target: "review", target_port: "b" },
    { source: "fetch", source_port: "c", target: "review", target_port: "c" },
  ],
  outputs: [{ name: "verdict", type: "string", source: "steps.review.outputs.verdict" }],
};

function props(over: Partial<CanvasProps> = {}): CanvasProps {
  return {
    workflow: THREE,
    machines: MACHINES,
    actors: [],
    overlay: null,
    selected: null,
    onSelect: vi.fn(),
    onToggle: vi.fn(),
    onPlacement: vi.fn(),
    onEdit: vi.fn(),
    onDelete: vi.fn(),
    onAddStep: vi.fn(),
    onOpenIo: vi.fn(),
    onConnect: vi.fn(),
    onRefused: vi.fn(),
    ...over,
  };
}

const canvas = () => screen.getByRole("region", { name: "Workflow canvas" });
const card = (name: string) => within(canvas()).getByRole("group", { name });
const edge = (id: string) => canvas().querySelector(`[data-testid="rf__edge-${CSS.escape(id)}"]`);
const edgeIds = () =>
  [...canvas().querySelectorAll(".react-flow__edge")].map((e) => e.getAttribute("data-id")).sort();

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the compact Detailed canvas", () => {
  it("draws three wires between two steps as one edge labelled 3, and no port rows", async () => {
    render(<WorkflowCanvas {...props()} />);
    await waitFor(() => expect(edge(bundleId("fetch", "review"))).not.toBeNull());
    // One edge per connected pair: in -> Fetch, Fetch -> Review (3 wires), Review -> out.
    expect(edgeIds()).toEqual(
      [bundleId("inputs", "fetch"), bundleId("fetch", "review"), bundleId("review", "outputs")].sort(),
    );
    const count = within(canvas()).getByRole("img", { name: "3 connections" });
    expect(count).toHaveTextContent(/^3$/);
    expect(edge(bundleId("fetch", "review"))).toHaveAttribute(
      "aria-label",
      "Fetch to Review, 3 connections, same machine",
    );
    // A single wire is a bundle too, but unlabelled.
    expect(within(canvas()).queryByRole("img", { name: "1 connection" })).toBeNull();
    for (const name of ["Inputs", "Fetch", "Review", "Outputs"]) {
      expect(card(name).querySelector("[data-port]")).toBeNull();
    }
    expect(card("Inputs")).toHaveTextContent("1 input");
    expect(card("Review")).toHaveTextContent("Review");
    expect(within(card("Review")).getByRole("switch", { name: "Review enabled" })).toBeInTheDocument();
  });

  it("selecting a step expands its ports and draws its wires port to port; deselecting collapses it", async () => {
    const { rerender } = render(<WorkflowCanvas {...props()} />);
    await waitFor(() => expect(edge(bundleId("fetch", "review"))).not.toBeNull());

    rerender(<WorkflowCanvas {...props({ selected: "review" })} />);
    await waitFor(() => expect(edge("fetch.a->review.a")).not.toBeNull());
    expect(edgeIds()).toEqual(
      [
        bundleId("inputs", "fetch"),
        "fetch.a->review.a",
        "fetch.b->review.b",
        "fetch.c->review.c",
        "review.verdict->outputs.verdict",
      ].sort(),
    );
    expect(edge(bundleId("fetch", "review"))).toBeNull();
    expect(edge("fetch.b->review.b")).toHaveAttribute("aria-label", "Fetch b to Review b, same machine");
    expect(within(canvas()).queryByRole("img", { name: "3 connections" })).toBeNull();
    const ports = [...card("Review").querySelectorAll("[data-port]")].map((p) => p.getAttribute("data-port"));
    expect(ports).toEqual(["in:a", "in:b", "in:c", "out:verdict"]);
    // Only the selected card expands.
    expect(card("Fetch").querySelector("[data-port]")).toBeNull();
    // Its port handles are connectable; the bundle handles never are.
    const handle = card("Review").querySelector('[data-port="in:a"] .react-flow__handle');
    expect(handle).toHaveClass("connectable");
    for (const bundle of canvas().querySelectorAll(".wf-handle--bundle")) {
      expect(bundle).not.toHaveClass("connectable");
    }

    rerender(<WorkflowCanvas {...props({ selected: null })} />);
    await waitFor(() => expect(edge(bundleId("fetch", "review"))).not.toBeNull());
    expect(card("Review").querySelector("[data-port]")).toBeNull();
  });

  it("Enter on a focused step selects it, as a click does", async () => {
    const onSelect = vi.fn();
    render(<WorkflowCanvas {...props({ onSelect })} />);
    await waitFor(() => expect(card("Review")).toBeInTheDocument());
    const node = card("Review");
    node.focus();
    fireEvent.keyDown(node, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("review");
  });
});
