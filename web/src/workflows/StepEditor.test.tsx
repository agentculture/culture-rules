import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import type { Actor, Step, WorkflowDef } from "../api/workflows";
import { StepEditor } from "./StepEditor";

const FULL: Step = {
  id: "build",
  name: "Build image",
  description: "Builds the image and tags it.",
  kind: "actor_task",
  inputs: [
    { name: "repo", type: "string", required: true, description: "Which repo" },
    { name: "tag", type: "string", required: false },
  ],
  outputs: [{ name: "digest", type: "string", required: false }],
  placement: { actor: "runner-1" },
  timeout_s: 90.5,
  retry: { max_attempts: 3, backoff_s: 2, backoff_multiplier: 1.5 },
  config: { command: "build", args: { repo: "culture", jobs: 4 }, future_key: { nested: [1, 2] }, note: "keep me" },
  max_iterations: null,
  enabled: true,
};

const RUNNER: Actor = {
  id: "runner-1",
  name: "Runner",
  kind: "runner",
  params: {
    commands: {
      build: { argv: ["make"], params: { repo: "string", jobs: "integer", clean: "boolean" } },
      test: { argv: ["pytest"], params: {} },
    },
  },
};

const wfWith = (step: Step): WorkflowDef => ({ id: "wf", name: "WF", steps: [step], edges: [] });

function renderEditor(step: Step = FULL, actors: Actor[] = [RUNNER]) {
  const changes: WorkflowDef[] = [];
  const onClose = vi.fn();
  function Host() {
    const [wf, setWf] = useState(wfWith(step));
    return (
      <StepEditor
        workflow={wf}
        stepId={step.id}
        actors={actors}
        onChange={(next) => {
          changes.push(next);
          setWf(next);
        }}
        onClose={onClose}
      />
    );
  }
  render(<Host />);
  const last = () => changes[changes.length - 1].steps![0];
  return { changes, onClose, last };
}

describe("StepEditor: every Step field", () => {
  it("round-trips a fully populated step unchanged", async () => {
    const { changes, onClose } = renderEditor();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(changes).toEqual([]);
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps every other field (unknown config keys included) when one field is edited", async () => {
    const { last } = renderEditor();
    const d = screen.getByLabelText("Description");
    await userEvent.type(d, "!");
    expect(last()).toEqual({ ...FULL, description: "Builds the image and tags it.!" });
  });

  it("edits description", async () => {
    const { last } = renderEditor({ id: "s", kind: "actor_task" });
    await userEvent.type(screen.getByLabelText("Description"), "hi");
    expect(last().description).toBe("hi");
  });

  it("edits timeout and clears it to unset", async () => {
    const { last } = renderEditor();
    const t = screen.getByLabelText("Timeout (seconds)");
    await userEvent.clear(t);
    expect(last().timeout_s).toBeNull();
    await userEvent.type(t, "30");
    expect(last().timeout_s).toBe(30);
  });

  it("edits each retry field", async () => {
    const { last } = renderEditor();
    const a = screen.getByLabelText("Max attempts");
    await userEvent.clear(a);
    await userEvent.type(a, "5");
    expect(last().retry).toEqual({ max_attempts: 5, backoff_s: 2, backoff_multiplier: 1.5 });
    const b = screen.getByLabelText("Backoff (seconds)");
    await userEvent.clear(b);
    await userEvent.type(b, "0");
    expect(last().retry).toEqual({ max_attempts: 5, backoff_s: 0, backoff_multiplier: 1.5 });
    const m = screen.getByLabelText("Backoff multiplier");
    await userEvent.clear(m);
    await userEvent.type(m, "2");
    expect(last().retry).toEqual({ max_attempts: 5, backoff_s: 0, backoff_multiplier: 2 });
  });

  it("clearing every retry field unsets retry", async () => {
    const { last } = renderEditor();
    for (const name of ["Max attempts", "Backoff (seconds)", "Backoff multiplier"]) {
      await userEvent.clear(screen.getByLabelText(name));
    }
    expect(last().retry).toBeNull();
  });

  it("toggles a port's required flag", async () => {
    const { last } = renderEditor();
    await userEvent.click(screen.getByLabelText("Input tag is required"));
    expect(last().inputs![1].required).toBe(true);
    await userEvent.click(screen.getByLabelText("Input repo is required"));
    expect(last().inputs![0]).toEqual({ name: "repo", type: "string", required: false, description: "Which repo" });
    await userEvent.click(screen.getByLabelText("Output digest is required"));
    expect(last().outputs![0].required).toBe(true);
  });

  it("offers a runner's commands and typed args, preserving unknown config keys", async () => {
    const { last } = renderEditor();
    const cmd = screen.getByLabelText("Command");
    expect(within(cmd).getAllByRole("option").map((o) => o.textContent)).toEqual(
      expect.arrayContaining(["build", "test"]),
    );
    await userEvent.click(screen.getByLabelText("clean"));
    expect(last().config).toEqual({ ...FULL.config, args: { repo: "culture", jobs: 4, clean: true } });
    const jobs = screen.getByLabelText("jobs");
    await userEvent.clear(jobs);
    await userEvent.type(jobs, "8");
    expect(last().config!.args).toEqual({ repo: "culture", jobs: 8, clean: true });
    await userEvent.selectOptions(cmd, "test");
    expect(last().config).toMatchObject({ command: "test", future_key: { nested: [1, 2] }, note: "keep me" });
  });

  it("edits config as key/value pairs for a non-runner step", async () => {
    const step: Step = { id: "s", kind: "actor_task", config: { a: "x", n: 5, deep: { k: 1 } } };
    const { last } = renderEditor(step, []);
    await userEvent.type(screen.getByLabelText("Config value a"), "y");
    expect(last().config).toEqual({ a: "xy", n: 5, deep: { k: 1 } });
    await userEvent.click(screen.getByRole("button", { name: "Add config entry" }));
    await userEvent.type(screen.getByLabelText("Config key 4"), "z");
    expect(last().config).toMatchObject({ z: "" });
    await userEvent.click(screen.getByRole("button", { name: "Remove config entry a" }));
    expect(last().config).not.toHaveProperty("a");
  });

  it("edits config as raw JSON behind the advanced toggle", async () => {
    const step: Step = { id: "s", kind: "actor_task", config: { a: 1 } };
    const { last } = renderEditor(step, []);
    expect(screen.queryByLabelText("Config JSON")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Edit config as JSON" }));
    const box = screen.getByLabelText("Config JSON");
    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste('{"b": [1]}');
    expect(last().config).toEqual({ b: [1] });
  });

  it("shows a guided error for broken JSON and blocks Done", async () => {
    const { changes } = renderEditor({ id: "s", kind: "actor_task", config: { a: 1 } }, []);
    await userEvent.click(screen.getByRole("button", { name: "Edit config as JSON" }));
    const box = screen.getByLabelText("Config JSON");
    await userEvent.clear(box);
    await userEvent.click(box);
    await userEvent.paste("{nope");
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(changes).toEqual([]);
    expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("StepEditor: guided validation", () => {
  it.each(["0", "-1", "x"])("rejects timeout %s with a guided error and blocks save", async (bad) => {
    const { changes } = renderEditor({ id: "s", kind: "actor_task" });
    await userEvent.type(screen.getByLabelText("Timeout (seconds)"), bad);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(changes).toEqual([]);
    expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it.each([
    ["Max attempts", "0"],
    ["Max attempts", "1.5"],
    ["Backoff (seconds)", "-1"],
    ["Backoff multiplier", "0.5"],
    ["Backoff multiplier", "x"],
  ])("rejects %s = %s", async (label, bad) => {
    const { changes } = renderEditor();
    const f = screen.getByLabelText(label);
    await userEvent.clear(f);
    await userEvent.type(f, bad);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(changes.at(-1)?.steps?.[0].retry).not.toMatchObject({ [keyOf(label)]: Number(bad) });
    expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("recovers when the value is fixed", async () => {
    renderEditor({ id: "s", kind: "actor_task" });
    const t = screen.getByLabelText("Timeout (seconds)");
    await userEvent.type(t, "0");
    expect(screen.getByRole("alert")).toBeTruthy();
    await userEvent.clear(t);
    await userEvent.type(t, "5");
    expect(screen.queryByRole("alert")).toBeNull();
    expect((screen.getByRole("button", { name: "Done" }) as HTMLButtonElement).disabled).toBe(false);
  });
});

function keyOf(label: string) {
  return { "Max attempts": "max_attempts", "Backoff (seconds)": "backoff_s", "Backoff multiplier": "backoff_multiplier" }[label]!;
}
