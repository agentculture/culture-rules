import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { WorkflowDef } from "../api/workflows";
import { REVIEW_PR } from "./fixture";
import { IoEditor } from "./IoEditor";

function renderEditor(side: "in" | "out", start: WorkflowDef = REVIEW_PR) {
  const changes: WorkflowDef[] = [];
  const onClose = vi.fn();
  function Host() {
    const [wf, setWf] = useState(start);
    return (
      <IoEditor
        workflow={wf}
        side={side}
        onChange={(next) => {
          changes.push(next);
          setWf(next);
        }}
        onClose={onClose}
      />
    );
  }
  render(<Host />);
  const last = () => changes[changes.length - 1];
  return { changes, onClose, last };
}

describe("IoEditor: the in node", () => {
  it("opens as the inputs editor with the workflow description", () => {
    renderEditor("in");
    const dialog = screen.getByRole("dialog", { name: "Edit inputs" });
    expect(within(dialog).getByLabelText("Workflow description")).toHaveValue(REVIEW_PR.description);
    expect(within(dialog).getByRole("textbox", { name: "Name of input pr" })).toHaveValue("pr");
    expect(within(dialog).getByRole("combobox", { name: "Type of input pr" })).toHaveValue("integer");
    expect(within(dialog).queryByRole("group", { name: "Variables" })).toBeNull();
  });

  it("edits the workflow description", async () => {
    const { last } = renderEditor("in", { ...REVIEW_PR, description: "" });
    await userEvent.type(screen.getByLabelText("Workflow description"), "Hi");
    expect(last().description).toBe("Hi");
  });

  it("adds an input, then types its name, type, required and description", async () => {
    const { last } = renderEditor("in");
    await userEvent.click(screen.getByRole("button", { name: "Add input" }));
    expect(last().inputs!.map((p) => p.name)).toEqual(["pr", "repo", "input1"]);
    const name = screen.getByRole("textbox", { name: "Name of input input1" });
    await userEvent.clear(name);
    // Clearing is refused (an input needs a name) with guidance; the draft keeps the old name.
    expect(screen.getByRole("alert")).toHaveTextContent("Something required is empty.");
    expect(last().inputs!.at(-1)!.name).toBe("input1");
    await userEvent.type(name, "branch");
    expect(screen.queryByRole("alert")).toBeNull();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type of input branch" }), "string");
    await userEvent.click(screen.getByRole("checkbox", { name: "Input branch is required" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Description of input branch" }), "Which branch");
    expect(last().inputs!.at(-1)).toEqual({
      name: "branch",
      type: "string",
      required: false,
      description: "Which branch",
    });
  });

  it("renaming an input follows the wires that read it", async () => {
    const { last } = renderEditor("in");
    const name = screen.getByRole("textbox", { name: "Name of input repo" });
    await userEvent.type(name, "sitory");
    expect(last().inputs!.map((p) => p.name)).toEqual(["pr", "repository"]);
    expect(last().edges!.filter((e) => e.source === "inputs").map((e) => e.source_port)).toEqual([
      "pr",
      "repository",
      "repository",
    ]);
  });

  it("refuses a duplicate name with guided text, never raw text", async () => {
    const { last } = renderEditor("in");
    const name = screen.getByRole("textbox", { name: "Name of input repo" });
    await userEvent.clear(name);
    await userEvent.type(name, "pr");
    expect(screen.getByRole("alert")).toHaveTextContent("Two things share the same name.");
    expect(last().inputs!.map((p) => p.name)).toEqual(["pr", "p"]);
    // Done is held back while a name is refused.
    expect(screen.getByRole("button", { name: "Done" })).toBeDisabled();
  });

  it("removes an input", async () => {
    const { last } = renderEditor("in");
    await userEvent.click(screen.getByRole("button", { name: "Remove input pr" }));
    expect(last().inputs!.map((p) => p.name)).toEqual(["repo"]);
    expect(last().edges!.some((e) => e.source === "inputs" && e.source_port === "pr")).toBe(false);
  });
});

describe("IoEditor: the out node", () => {
  it("opens as the outputs and variables editor", () => {
    renderEditor("out");
    const dialog = screen.getByRole("dialog", { name: "Edit outputs" });
    expect(within(dialog).getByRole("group", { name: "Outputs" })).toBeInTheDocument();
    expect(within(dialog).getByRole("group", { name: "Variables" })).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox", { name: "verdict comes from" })).toHaveValue(
      "steps.decide.outputs.verdict",
    );
  });

  it("adds an output and picks a step output port as its source", async () => {
    const { last } = renderEditor("out");
    await userEvent.click(screen.getByRole("button", { name: "Add output" }));
    const from = screen.getByRole("combobox", { name: "output1 comes from" });
    const labels = within(from).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toContain("Fetch diff · diff");
    expect(labels).toContain("Inputs · pr");
    await userEvent.selectOptions(from, "steps.fetch-diff.outputs.diff");
    expect(last().outputs!.at(-1)).toEqual({ name: "output1", type: "any", source: "steps.fetch-diff.outputs.diff" });
  });

  it("renames, retypes and removes outputs", async () => {
    const { last } = renderEditor("out");
    const name = screen.getByRole("textbox", { name: "Name of output owner" });
    await userEvent.clear(name);
    await userEvent.type(name, "who");
    expect(last().outputs!.map((o) => o.name)).toEqual(["verdict", "who"]);
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type of output verdict" }), "boolean");
    expect(last().outputs![0]).toEqual({ name: "verdict", type: "boolean", source: null });
    await userEvent.click(screen.getByRole("button", { name: "Remove output who" }));
    expect(last().outputs!.map((o) => o.name)).toEqual(["verdict"]);
  });

  it("keeps the textual source inspectable and editable as an advanced field", async () => {
    const { last } = renderEditor("out");
    await userEvent.click(screen.getByText("Sources as text"));
    const text = screen.getByRole("textbox", { name: "Source of output verdict" });
    expect(text).toHaveValue("steps.decide.outputs.verdict");
    await userEvent.clear(text);
    await userEvent.type(text, "steps.nope");
    expect(screen.getByRole("alert")).toHaveTextContent("A reference points at something that does not exist.");
    await userEvent.clear(text);
    await userEvent.type(text, "inputs.repo");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(last().outputs![0].source).toBe("inputs.repo");
  });

  it("adds a variable, names and types it, and sets its default as JSON", async () => {
    const { last } = renderEditor("out");
    await userEvent.click(screen.getByRole("button", { name: "Add variable" }));
    expect(last().variables).toEqual([{ name: "var1", type: "any" }]);
    const name = screen.getByRole("textbox", { name: "Name of variable var1" });
    await userEvent.clear(name);
    await userEvent.type(name, "seen");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Type of variable seen" }), "array");
    const def = screen.getByRole("textbox", { name: "Default of variable seen" });
    await userEvent.type(def, "[[1,"); // "[[" types one "["
    expect(screen.getByRole("alert")).toHaveTextContent("not in a shape");
    await userEvent.type(def, "2]");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(last().variables).toEqual([{ name: "seen", type: "array", default: [1, 2] }]);
    // A variable is a source an output can read.
    const from = screen.getByRole("combobox", { name: "owner comes from" });
    expect(within(from).queryByRole("option", { name: "Variables · seen" })).toBeNull(); // array ≠ string
    await userEvent.click(screen.getByRole("button", { name: "Remove variable seen" }));
    expect(last().variables).toEqual([]);
  });

  it("Done closes", async () => {
    const { onClose, changes } = renderEditor("out");
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onClose).toHaveBeenCalled();
    expect(changes).toEqual([]);
  });
});
