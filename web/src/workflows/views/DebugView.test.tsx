import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkflowDef } from "../../api/workflows";
import DebugView from "./DebugView";

/** A carried input behind a wire, and a conditional result step: two different conditions. */
const WF: WorkflowDef = {
  id: "f",
  name: "f",
  steps: [
    {
      id: "loop",
      kind: "retry_until",
      max_iterations: 3,
      config: { carry: { note: "note", hint: "hint" } },
      inputs: [],
      outputs: [],
      body: [
        { id: "src", kind: "code", inputs: [{ name: "hint", type: "string" }], outputs: [{ name: "note", type: "string" }] },
        {
          id: "use",
          kind: "code",
          inputs: [{ name: "note", type: "string" }],
          outputs: [{ name: "note", type: "string" }, { name: "hint", type: "string" }],
        },
        {
          id: "maybe",
          kind: "code",
          config: { when: { field: "x", op: "eq", value: 1 } },
          inputs: [],
          outputs: [{ name: "hint", type: "string" }],
        },
      ],
    },
  ],
  edges: [{ source: "src", source_port: "note", target: "use", target_port: "note" }],
  outputs: [],
};

const port = (ref: string) =>
  within(screen.getByRole("region", { name: "Workflow ports" })).getByRole("button", {
    name: new RegExp(`^${ref.replace(/\./g, "\\.")},`),
  });

describe("the Debug view's carry and fallback labels", () => {
  it("a carried value behind a wire says the wire must supply nothing, not that a step runs", () => {
    render(<DebugView workflow={WF} />);
    const note = port("steps.use.inputs.note");
    expect(note).toHaveTextContent("↻ steps.use.outputs.note · if the wire supplies nothing");
    expect(note).not.toHaveTextContent("if use runs");
    expect(note).toHaveAccessibleName(/then carried from steps\.use\.outputs\.note if the wire supplies nothing/);
    expect(note.getAttribute("aria-label")).not.toMatch(/if use runs/);
  });

  it("a carried value from a conditional step says the step must run", () => {
    render(<DebugView workflow={WF} />);
    const hint = port("steps.src.inputs.hint");
    expect(hint).toHaveTextContent("↻ steps.maybe.outputs.hint · if maybe runs");
    expect(hint).not.toHaveTextContent("wire supplies nothing");
    expect(hint).toHaveAccessibleName(/then carried from steps\.maybe\.outputs\.hint if maybe runs/);
  });
});
