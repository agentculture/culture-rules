import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MACHINES } from "../../fixtures/rules-fixture";
import { DOMMatrixStub, MeasuringResizeObserver, useMeasuredLayout } from "../../test/reactFlow";
import { ACTORS, REVIEW_PR } from "../fixture";
import { VIEW_MODE_KEY } from "./mode";
import WorkflowViews, { type WorkflowViewsProps } from "./WorkflowViews";

useMeasuredLayout();

function props(over: Partial<WorkflowViewsProps> = {}): WorkflowViewsProps {
  return {
    workflow: REVIEW_PR,
    machines: MACHINES,
    actors: ACTORS,
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

const switchGroup = () => screen.getByRole("group", { name: "Canvas view" });
const modeButton = (name: string) => within(switchGroup()).getByRole("button", { name });
const debug = () => screen.getByRole("region", { name: "Workflow ports" });
const port = (ref: string) => within(debug()).getByRole("button", { name: new RegExp(`^${ref.replace(/\./g, "\\.")}\\b`) });

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("the Simple / Detailed / Debug switch", () => {
  it("shows three modes, Simple pressed, and a placeholder in the Simple slot", () => {
    render(<WorkflowViews {...props()} />);
    expect(within(switchGroup()).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Simple",
      "Detailed",
      "Debug",
    ]);
    expect(modeButton("Simple")).toHaveAttribute("aria-pressed", "true");
    expect(modeButton("Detailed")).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("region", { name: "Simple view" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Workflow canvas" })).not.toBeInTheDocument();
  });

  it("renders the Simple view a caller passes in place of the placeholder", () => {
    render(<WorkflowViews {...props({ simple: <p>When / Then</p> })} />);
    expect(screen.getByText("When / Then")).toBeInTheDocument();
  });

  it("Detailed renders today's steps-and-edges canvas", async () => {
    const user = userEvent.setup();
    render(<WorkflowViews {...props()} />);
    await user.click(modeButton("Detailed"));
    expect(modeButton("Detailed")).toHaveAttribute("aria-pressed", "true");
    const canvas = screen.getByRole("region", { name: "Workflow canvas" });
    // the same React Flow canvas: in, the four step cards, out
    await waitFor(() => expect(within(canvas).getByRole("group", { name: "Review" })).toBeInTheDocument());
    for (const name of ["Inputs", "Fetch diff", "Run tests", "Decide", "Outputs"]) {
      expect(within(canvas).getByRole("group", { name })).toBeInTheDocument();
    }
    await waitFor(() => expect(canvas.querySelectorAll(".react-flow__edge").length).toBeGreaterThan(0));
    expect(screen.queryByRole("region", { name: "Workflow ports" })).not.toBeInTheDocument();
  });

  it("Debug renders every port with its type and reference", async () => {
    const user = userEvent.setup();
    render(<WorkflowViews {...props()} />);
    await user.click(modeButton("Debug"));
    const refs = [
      "inputs.pr",
      "inputs.repo",
      "steps.fetch-diff.inputs.pr",
      "steps.fetch-diff.inputs.repo",
      "steps.fetch-diff.outputs.diff",
      "steps.run-tests.inputs.repo",
      "steps.run-tests.outputs.passed",
      "steps.review.inputs.diff",
      "steps.review.outputs.findings",
      "steps.review.outputs.owner",
      "steps.decide.inputs.findings",
      "steps.decide.inputs.passed",
      "steps.decide.outputs.verdict",
      "outputs.verdict",
      "outputs.owner",
    ];
    const buttons = within(debug()).getAllByRole("button", { name: /^(inputs|outputs|steps)\./ });
    expect(buttons.map((b) => b.dataset.ref).sort()).toEqual([...refs].sort());
    const diff = port("steps.review.inputs.diff");
    expect(diff).toHaveTextContent("diff");
    expect(diff).toHaveTextContent("string");
    expect(diff).toHaveTextContent("← steps.fetch-diff.outputs.diff");
    expect(port("steps.fetch-diff.inputs.pr")).toHaveTextContent("integer");
    expect(port("outputs.verdict")).toHaveTextContent("← steps.decide.outputs.verdict");
    expect(within(debug()).getByText("All required ports bound")).toBeInTheDocument();
  });

  it("selecting a port in Debug highlights its upstream and downstream ports", async () => {
    const user = userEvent.setup();
    render(<WorkflowViews {...props()} />);
    await user.click(modeButton("Debug"));
    await user.click(port("steps.review.outputs.findings"));

    expect(port("steps.review.outputs.findings")).toHaveAttribute("aria-pressed", "true");
    const related = (ref: string) => port(ref).dataset.related;
    expect(related("steps.review.outputs.findings")).toBe("selected");
    expect(related("steps.review.inputs.diff")).toBe("upstream");
    expect(related("steps.fetch-diff.outputs.diff")).toBe("upstream");
    expect(related("steps.decide.inputs.findings")).toBe("downstream");
    expect(related("inputs.pr")).toBe("none");
    expect(related("outputs.verdict")).toBe("none");

    const panel = screen.getByRole("region", { name: "Selected port" });
    expect(within(panel).getByText("steps.review.outputs.findings")).toBeInTheDocument();
    expect(within(panel).getByRole("list", { name: "Upstream" })).toHaveTextContent("steps.fetch-diff.outputs.diff");
    expect(within(panel).getByRole("list", { name: "Downstream" })).toHaveTextContent("steps.decide.inputs.findings");

    // everything downstream reaches through decide to the workflow output
    await user.click(within(panel).getByRole("button", { name: "Show everything downstream" }));
    expect(related("steps.decide.outputs.verdict")).toBe("downstream");
    expect(related("outputs.verdict")).toBe("downstream");

    // a second click on the selected port, or Close, clears the highlight
    await user.click(within(panel).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("region", { name: "Selected port" })).not.toBeInTheDocument();
    expect(related("steps.review.inputs.diff")).toBe("none");
    expect(port("steps.review.outputs.findings")).toHaveAttribute("aria-pressed", "false");
  });

  it("returns focus to the selected port when Close or Escape dismisses the panel", async () => {
    const user = userEvent.setup();
    render(<WorkflowViews {...props()} />);
    await user.click(modeButton("Debug"));
    await user.click(port("steps.review.inputs.diff"));
    const panel = screen.getByRole("region", { name: "Selected port" });
    await user.click(within(panel).getByRole("button", { name: "Close" }));
    expect(port("steps.review.inputs.diff")).toHaveFocus();

    await user.click(port("steps.decide.inputs.passed"));
    await user.click(within(screen.getByRole("region", { name: "Selected port" })).getByRole("button", { name: "Copy reference" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("region", { name: "Selected port" })).not.toBeInTheDocument();
    expect(port("steps.decide.inputs.passed")).toHaveFocus();
  });

  it("keeps the chosen mode per viewer across a remount", async () => {
    const user = userEvent.setup();
    const first = render(<WorkflowViews {...props()} />);
    await user.click(modeButton("Debug"));
    expect(window.localStorage.getItem(VIEW_MODE_KEY)).toBe("debug");
    first.unmount();

    render(<WorkflowViews {...props()} />);
    expect(modeButton("Debug")).toHaveAttribute("aria-pressed", "true");
    expect(debug()).toBeInTheDocument();
  });

  it("still switches when localStorage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    const user = userEvent.setup();
    render(<WorkflowViews {...props()} />);
    expect(modeButton("Simple")).toHaveAttribute("aria-pressed", "true");
    await user.click(modeButton("Debug"));
    expect(modeButton("Debug")).toHaveAttribute("aria-pressed", "true");
    expect(debug()).toBeInTheDocument();
  });
});
