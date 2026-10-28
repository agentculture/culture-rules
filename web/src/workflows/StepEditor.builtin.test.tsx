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

describe("attachment.fetch built-in", () => {
  it("sets config.builtin and its typed ports: attachments in, items out (files, d7)", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "fetch", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "attachment.fetch");
    const s = last();
    expect(s.config).toEqual({ builtin: "attachment.fetch" });
    expect(s.inputs?.map((p) => [p.name, p.type, p.required !== false])).toEqual([
      ["attachments", "array", true],
      ["output_root", "string", false],
      ["retention_days", "integer", false],
    ]);
    expect(s.outputs?.map((p) => p.name)).toEqual(["items", "fetched", "skipped"]);
  });
});

describe("cursor.advance built-in", () => {
  it("sets its ports: key, expected (optional) and value in; advanced out", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "advance", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "cursor.advance");
    const s = last();
    expect(s.config).toEqual({ builtin: "cursor.advance" });
    expect(s.inputs?.map((p) => [p.name, p.required !== false])).toEqual([
      ["key", true],
      ["expected", false],
      ["value", true],
    ]);
    expect(s.outputs?.map((p) => p.name)).toEqual(["key", "value", "previous", "advanced"]);
  });
});

describe("document.sections built-in", () => {
  it("sets config.builtin", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "split", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "document.sections");
    expect(last().config).toEqual({ builtin: "document.sections" });
  });

  it("gives every source an optional input port", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "split", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "document.sections");
    expect(last().inputs?.map((p) => [p.name, p.required !== false])).toEqual([
      ["content_base64", false],
      ["html", false],
      ["markdown", false],
      ["content_path", false],
      ["content_root", false],
      ["format", false],
    ]);
  });

  it("gives the sections, count and found outputs", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "split", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "document.sections");
    expect(last().outputs?.map((p) => p.name)).toEqual(["sections", "count", "found"]);
  });
});

describe("keyset built-ins", () => {
  it("sets config.builtin for keyset.filter", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "fresh", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "keyset.filter");
    expect(last().config).toEqual({ builtin: "keyset.filter" });
  });

  it("gives keyset.filter the items, kept, dropped and keyless outputs", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "fresh", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "keyset.filter");
    expect(last().outputs?.map((p) => p.name)).toEqual(["items", "kept", "dropped", "keyless"]);
  });

  it("gives keyset.add the added and already_present outputs", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "remember", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "keyset.add");
    expect(last().outputs?.map((p) => p.name)).toEqual(["added", "already_present"]);
  });
});
