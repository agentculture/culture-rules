import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { ActionCatalogue, ActionKindSpec, ActionParam } from "../api/actions";
import type { Action } from "../api/types";
import { ACTION_CATALOGUE } from "../fixtures/action-catalogue";
import { ACTORS } from "../fixtures/rules-fixture";
import ActionPicker, { blankAction, clearedTargets, viewOf } from "./ActionPicker";

/* d11: the editor keeps no per-kind shims; the blank action, a legacy form and the command
 * widgets all come from what the catalogue serves. */

const param = (name: string, extra: Partial<ActionParam> = {}): ActionParam => ({
  name,
  type: "str",
  required: false,
  label: "",
  widget: "",
  choices: [],
  options_from: "",
  choose: "",
  needs_actor_param: "",
  needed: false,
  advanced: false,
  args_for: "",
  ...extra,
});

const spec = (kind: string, params: ActionParam[], extra: Partial<ActionKindSpec> = {}): ActionKindSpec => ({
  kind,
  label: kind,
  summary: kind,
  lenient: false,
  aliases: [],
  params,
  any_of: [],
  actors: [{ kinds: ["runner"] }],
  actorless: false,
  declarable_by_apps: false,
  shown_as: null,
  ...extra,
});

/* A runner kind whose command and arguments params are not named command and args. */
const RUN = spec("acme.run", [
  param("actor", { required: true }),
  param("task", { required: true, widget: "command" }),
  param("task_inputs", { type: "dict", widget: "command_args", args_for: "task" }),
]);
/* An older kind whose stored form, when it names a target, is shown as acme.run. */
const OLD = spec("acme.old_run", [param("target")], { shown_as: { when_param: "target", kind: "acme.run" } });
const WITH_RUN: ActionCatalogue = { items: [RUN, OLD, ...ACTION_CATALOGUE.items] };

function Harness({ start, catalogue }: Readonly<{ start: Action; catalogue: ActionCatalogue }>) {
  const [value, setValue] = useState<Action>(start);
  return (
    <>
      <ActionPicker value={value} actors={ACTORS} catalogue={catalogue} onChange={setValue} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Action;

describe("the blank action", () => {
  it("is the first kind in catalogue order, without params when the kind takes none", () => {
    expect(blankAction(ACTION_CATALOGUE)).toEqual({ kind: "noop" });
  });

  it("starts an empty params mapping for a kind that has params", () => {
    expect(blankAction(WITH_RUN)).toEqual({ kind: "acme.run", params: {} });
  });

  it("is the named kind when one is given, keeping the name", () => {
    expect(blankAction(ACTION_CATALOGUE, "message", "Notify")).toEqual({ kind: "message", params: {}, name: "Notify" });
  });

  it("is what the picker shows when it is given no value", () => {
    render(<ActionPicker actors={ACTORS} catalogue={WITH_RUN} onChange={() => {}} />);
    expect(screen.getByLabelText("What happens")).toHaveValue("acme.run");
  });
});

describe("a served shown_as hint", () => {
  it("shows a stored action that sets the param as the named kind", () => {
    const stored = { kind: "acme.old_run", params: { target: "ci-runner" } };
    expect(viewOf(stored, WITH_RUN)).toEqual({ ...stored, kind: "acme.run" });
  });

  it("leaves a stored action without the param as it is", () => {
    const stored = { kind: "acme.old_run", params: {} };
    expect(viewOf(stored, WITH_RUN)).toBe(stored);
  });
});

describe("the command widgets", () => {
  it("work with command and arguments params named as the kind names them", async () => {
    const user = userEvent.setup();
    render(<Harness catalogue={WITH_RUN} start={{ kind: "acme.run", params: {} }} />);
    await user.selectOptions(screen.getByLabelText("Actor"), "ci-runner");
    await user.selectOptions(screen.getByLabelText("Task"), "disk-free");
    await user.type(screen.getByLabelText("path"), "/data");
    expect(saved()).toEqual({
      kind: "acme.run",
      params: { actor: "ci-runner", task: "disk-free", task_inputs: { path: "/data" } },
    });
  });

  it("clear the arguments when another command is chosen", async () => {
    const user = userEvent.setup();
    const start = { kind: "acme.run", params: { actor: "ci-runner", task: "disk-free", task_inputs: { path: "/data" } } };
    render(<Harness catalogue={WITH_RUN} start={start} />);
    await user.selectOptions(screen.getByLabelText("Task"), "gpu-temp");
    expect(saved().params).toEqual({ actor: "ci-runner", task: "gpu-temp" });
  });
});

describe("an actor change clears the channel params by their served widget", () => {
  it("clears a discord_channel param whatever it is named", () => {
    const post = spec("acme.post", [param("actor"), param("target", { widget: "discord_channel" })]);
    expect(clearedTargets(post)).toEqual({ target: undefined });
  });

  it("clears nothing, and adds no channel key, for a kind with no channel param", () => {
    expect(clearedTargets(RUN)).toEqual({});
  });
});
