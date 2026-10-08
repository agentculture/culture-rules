import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { ACTORS } from "../fixtures/rules-fixture";
import type { Trigger } from "../api/types";
import TriggerPicker, { blankTrigger, triggerProblem } from "./TriggerPicker";

/** Holds the picked trigger so a test can read exactly what would be saved. */
function Harness({ start }: Readonly<{ start?: Trigger }>) {
  const [value, setValue] = useState<Trigger>(start ?? blankTrigger("event"));
  return (
    <>
      <TriggerPicker value={value} actors={ACTORS} onChange={setValue} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Trigger;

describe("event triggers", () => {
  it("picks a surface, then a declared event, and writes params.type exactly", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByLabelText("Surface"), "github-app");
    await user.selectOptions(screen.getByLabelText("Event"), "github.pr.opened");
    expect(saved()).toEqual({ kind: "event", params: { type: "github.pr.opened" } });
  });

  it("offers only the declared events of enabled apps, and no free-text field for the type", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const surface = screen.getByLabelText("Surface");
    expect(within(surface).queryByRole("option", { name: /Discord/ })).not.toBeInTheDocument();
    await user.selectOptions(surface, "github-app");
    const options = within(screen.getByLabelText("Event"))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(options).toEqual(["", "github.pr.opened", "github.push"]);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("preselects an existing event and validates that one is picked", () => {
    render(<Harness start={{ kind: "event", params: { type: "github.push" } }} />);
    expect(screen.getByLabelText("Surface")).toHaveValue("github-app");
    expect(screen.getByLabelText("Event")).toHaveValue("github.push");
    expect(triggerProblem(saved())).toBeNull();
    expect(triggerProblem({ kind: "event", params: { type: "" } })).toBe("trigger_type_required");
    expect(triggerProblem({ kind: "schedule", params: { cron: "" } })).toBe("invalid");
  });
});

describe("run-finished triggers (d21)", () => {
  it("offers the engine's run events as a built-in surface", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.selectOptions(screen.getByLabelText("Surface"), "@rules-engine");
    const options = within(screen.getByLabelText("Event"))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(options).toEqual([
      "",
      "rules.run.succeeded",
      "rules.run.failed",
      "rules.run.cancelled",
      "rules.run.superseded",
    ]);
    await user.selectOptions(screen.getByLabelText("Event"), "rules.run.succeeded");
    expect(saved()).toEqual({ kind: "event", params: { type: "rules.run.succeeded" } });
  });

  it("preselects the engine surface for a stored run-event trigger", () => {
    render(<Harness start={{ kind: "event", params: { type: "rules.run.failed" } }} />);
    expect(screen.getByLabelText("Surface")).toHaveValue("@rules-engine");
    expect(screen.getByLabelText("Event")).toHaveValue("rules.run.failed");
  });

  it("asks for a condition on the run's workflow, only for run events", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    expect(screen.queryByTestId("run-event-hint")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Surface"), "@rules-engine");
    expect(screen.getByTestId("run-event-hint")).toHaveTextContent("data.workflow_id");
  });
});

describe("schedule triggers", () => {
  it("writes a preset's cron", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "On a schedule" }));
    await user.selectOptions(screen.getByLabelText("Repeat"), "Every hour");
    expect(saved()).toMatchObject({ kind: "schedule", params: { cron: "0 * * * *" } });
  });

  it("writes a custom cron and time zone", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "On a schedule" }));
    await user.selectOptions(screen.getByLabelText("Repeat"), "Custom");
    await user.clear(screen.getByLabelText("Cron"));
    await user.type(screen.getByLabelText("Cron"), "30 2 * * 1");
    await user.type(screen.getByLabelText("Time zone"), "Asia/Jerusalem");
    expect(saved()).toEqual({
      kind: "schedule",
      params: { cron: "30 2 * * 1", tz: "Asia/Jerusalem" },
    });
  });
});

describe("probe triggers", () => {
  it("writes actor, command, mode and schedule", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "A probe" }));
    await user.selectOptions(screen.getByLabelText("Actor"), "ci-runner");
    await user.selectOptions(screen.getByLabelText("Command"), "disk-free");
    await user.selectOptions(screen.getByLabelText("Mode"), "condition");
    await user.selectOptions(screen.getByLabelText("Repeat"), "Every 5 minutes");
    expect(saved()).toEqual({
      kind: "probe",
      params: { actor: "ci-runner", command: "disk-free", mode: "condition", schedule: "*/5 * * * *" },
    });
  });
});

describe("manual and existing triggers", () => {
  it("manual takes no params", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("radio", { name: "Manually" }));
    expect(saved()).toEqual({ kind: "manual" });
  });

  it("preselects the values of an existing typed rule", () => {
    render(
      <Harness
        start={{
          kind: "probe",
          params: { actor: "ci-runner", command: "gpu-temp", mode: "change", schedule: "0 9 * * *" },
        }}
      />,
    );
    expect(screen.getByRole("radio", { name: "A probe" })).toBeChecked();
    expect(screen.getByLabelText("Actor")).toHaveValue("ci-runner");
    expect(screen.getByLabelText("Command")).toHaveValue("gpu-temp");
    expect(screen.getByLabelText("Mode")).toHaveValue("change");
    expect(screen.getByLabelText("Repeat")).toHaveValue("0 9 * * *");
  });

  it("states a cron in words", () => {
    render(<Harness start={{ kind: "schedule", params: { cron: "*/5 * * * *" } }} />);
    expect(screen.getByTestId("cron-words")).toHaveTextContent("Every 5 minutes");
  });
});
