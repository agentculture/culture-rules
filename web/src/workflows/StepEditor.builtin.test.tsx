import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Step, WorkflowDef } from "../api/workflows";
import { StepEditor } from "./StepEditor";

/* A code step can be made a markdown.render built-in: its config and its typed ports. */

function setup(step: Step) {
  const changes: WorkflowDef[] = [];
  function Host() {
    const [wf, setWf] = useState<WorkflowDef>({ id: "wf", name: "WF", steps: [step], edges: [] });
    return (
      <StepEditor workflow={wf} stepId={step.id} onChange={(n) => { changes.push(n); setWf(n); }} onClose={() => {}} />
    );
  }
  render(<Host />);
  return () => changes[changes.length - 1].steps![0];
}

describe("markdown.render built-in", () => {
  it("is offered only on a code step", () => {
    setup({ id: "s", kind: "actor_task" });
    expect(screen.queryByLabelText("Built-in")).not.toBeInTheDocument();
  });

  it("sets config.builtin and the title/text ports (author, source, url, id optional)", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "render", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "markdown.render");
    const s = last();
    expect(s.config).toEqual({ builtin: "markdown.render" });
    expect(s.inputs?.map((p) => [p.name, p.required !== false])).toEqual([
      ["title", true],
      ["text", true],
      ["author", false],
      ["source", false],
      ["url", false],
      ["id", false],
    ]);
    expect(s.outputs?.map((p) => p.name)).toEqual(["markdown", "content_base64"]);
  });

  it("keeps existing ports and other config, and None removes only the built-in", async () => {
    const user = userEvent.setup();
    const last = setup({
      id: "render",
      kind: "code",
      config: { builtin: "markdown.render", title: "Fixed" },
      inputs: [{ name: "text", type: "string" }],
    });
    expect(screen.getByLabelText("Built-in")).toHaveValue("markdown.render");
    await user.selectOptions(screen.getByLabelText("Built-in"), "");
    expect(last().config).toEqual({ title: "Fixed" });
    expect(last().inputs).toEqual([{ name: "text", type: "string" }]);
  });
});
