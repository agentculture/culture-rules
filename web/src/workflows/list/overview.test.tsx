import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Rule } from "../../api/types";
import type { Step, WorkflowDef } from "../../api/workflows";
import { foldModel } from "../../fold/model";
import { Overview, appsTouched, stepCount, type ActorRef } from "./Overview";

const docs = (dir: string) => resolve(dirname(fileURLToPath(import.meta.url)), "../../../../docs/rules", dir);
function load<T>(dir: string): T[] {
  return readdirSync(docs(dir)).filter((n) => n.endsWith(".json")).sort()
    .map((n) => JSON.parse(readFileSync(resolve(docs(dir), n), "utf8")) as T);
}

const step = (id: string, extra: Partial<Step> = {}): Step => ({ id, kind: "logic", ...extra });
const action = (id: string, actor: string): Step =>
  step(id, { config: { builtin: "action", action: { kind: "message", params: { actor } } } });
const wf = (id: string, steps: Step[], extra: Partial<WorkflowDef> = {}): WorkflowDef =>
  ({ id, name: id.toUpperCase(), version: 1, steps, ...extra }) as WorkflowDef;
const app = (id: string, surface: string): ActorRef => ({ id, kind: "app", params: { surface } });
const rule = (id: string, workflow: string, then: unknown): Rule =>
  ({ id, name: id, trigger: { kind: "event", params: { type: "x" } }, workflow: { id: workflow }, action: then }) as unknown as Rule;
const none = foldModel([], []);

function show(workflows: WorkflowDef[], opts: { actors?: ActorRef[]; rules?: Rule[]; error?: string | null } = {}) {
  const props = { onNew: vi.fn(), onNewRule: vi.fn() };
  render(
    <MemoryRouter>
      <Overview workflows={workflows} model={foldModel(opts.rules ?? [], workflows)} actors={opts.actors ?? []}
        error={opts.error} {...props} />
    </MemoryRouter>,
  );
  return props;
}

describe("stepCount", () => {
  it("counts top-level steps and loop bodies", () => {
    expect(stepCount(undefined)).toBe(0);
    expect(stepCount([step("a"), step("b", { kind: "for_each", body: [step("c"), step("d", { kind: "retry_until", body: [step("e")] })] })])).toBe(5);
  });

  it("gives the committed workflows their full counts (daily-newspaper 13, pr-fix 14)", () => {
    const all = [...load<WorkflowDef>("newspaper/workflows"), ...load<WorkflowDef>("pr-fixer/workflows")];
    const byId = Object.fromEntries(all.map((w) => [w.id, stepCount(w.steps)]));
    expect(byId["daily-newspaper"]).toBe(13);
    expect(byId["pr-fix"]).toBe(14);
    expect(all.some((w) => (w.steps ?? []).length < stepCount(w.steps))).toBe(true);
  });
});

describe("appsTouched", () => {
  const actors = [app("gh", "github"), app("mail", "email"), app("bad", "carrier-pigeon"),
    { id: "bot", kind: "agent" }, { id: "svc", kind: "service", params: { surface: "github" } }];

  it("takes app actors with a known surface from action steps, loop bodies, ai/actor_task steps and Then actions", () => {
    const flow = wf("a", [
      action("one", "gh"),
      step("loop", { kind: "for_each", body: [action("inner", "mail")] }),
      step("think", { kind: "ai", placement: { actor: "bot" } }),
    ]);
    expect(appsTouched(flow, none, actors)).toEqual(["Email", "GitHub"]);
    const task = wf("b", [step("t", { kind: "actor_task", placement: { actor: "mail" } })]);
    expect(appsTouched(task, none, actors)).toEqual(["Email"]);
    const started = wf("c", []);
    const model = foldModel([rule("r", "c", { kind: "message", params: { actor: "gh" } })], [started]);
    expect(appsTouched(started, model, actors)).toEqual(["GitHub"]);
  });

  it("leaves out unknown ids, non-app actors and unknown surfaces", () => {
    const flow = wf("a", [action("x", "ghost"), action("y", "svc"), action("z", "bad"), action("w", "bot")]);
    expect(appsTouched(flow, none, actors)).toEqual([]);
  });

  it("reads the committed newspaper: email and search apps, not the agent or the service", () => {
    const newspaper = load<WorkflowDef>("newspaper/workflows")[0];
    const list = load<ActorRef>("newspaper/actors");
    const model = foldModel(load<Rule>("newspaper/rules"), [newspaper]);
    expect(appsTouched(newspaper, model, list)).toEqual(["Email", "Search"]);
  });
});

describe("Overview", () => {
  it("renders one card per workflow with name, description, step count and apps; no list pane", () => {
    const flows = [
      wf("alpha", [action("a", "gh"), step("l", { kind: "for_each", body: [step("b"), step("c")] })], { description: "Sends the thing." }),
      wf("beta", [step("only")]),
      wf("gamma", [], { enabled: false }),
    ];
    show(flows, { actors: [app("gh", "github")] });
    const cards = within(screen.getByRole("list", { name: "All workflows" })).getAllByRole("listitem");
    expect(cards).toHaveLength(3);
    expect(within(cards[0]).getByRole("heading", { name: "ALPHA" })).toBeInTheDocument();
    expect(within(cards[0]).getByText("Sends the thing.")).toBeInTheDocument();
    expect(within(cards[0]).getByText("4 steps")).toBeInTheDocument();
    expect(within(cards[0]).getByText("GitHub")).toBeInTheDocument();
    expect(within(cards[1]).getByText("No description yet")).toBeInTheDocument();
    expect(within(cards[1]).getByText("1 step")).toBeInTheDocument();
    expect(within(cards[2]).getByText("0 steps")).toBeInTheDocument();
    expect(within(cards[2]).getByText("Disabled")).toBeInTheDocument();
    expect(within(cards[0]).getByRole("link")).toHaveAttribute("href", "/workflows?id=alpha");
    expect(screen.queryByRole("navigation", { name: "Workflows" })).toBeNull();
  });

  it("a whitespace description reads as none", () => {
    show([wf("a", [], { description: "   " })]);
    expect(screen.getByText("No description yet")).toBeInTheDocument();
  });

  it("shows the full step count of a loop workflow", () => {
    const newspaper = load<WorkflowDef>("newspaper/workflows");
    show(newspaper);
    expect(screen.getByText("13 steps")).toBeInTheDocument();
  });

  it("has an empty state with New workflow", () => {
    const { onNew } = show([]);
    expect(screen.getByRole("heading", { level: 1, name: "No workflows yet" })).toBeInTheDocument();
    expect(screen.queryByRole("list", { name: "All workflows" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "New workflow" }));
    expect(onNew).toHaveBeenCalledOnce();
  });

  it("has an error state naming what failed, and no cards", () => {
    show([], { error: "boom" });
    expect(screen.getByRole("alert")).toHaveTextContent("Could not list the workflows: boom");
    expect(screen.queryByRole("list", { name: "All workflows" })).toBeNull();
  });

  it("offers New workflow and New rule", () => {
    const { onNew, onNewRule } = show([wf("a", [])]);
    fireEvent.click(screen.getByRole("button", { name: "New workflow" }));
    fireEvent.click(screen.getByRole("button", { name: "New rule" }));
    expect(onNew).toHaveBeenCalledOnce();
    expect(onNewRule).toHaveBeenCalledOnce();
  });
});
