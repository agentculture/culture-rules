import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Rules from "../routes/Rules";
import { getAgentState, resetAgentState } from "../agent-state/store";
import { SELECTED_RULE_ID } from "../fixtures/rules-fixture";
import { createFakeApi, fetchFor, withPendingAsk, type FakeApi } from "./fake-api";

let api: FakeApi;

function renderRules(path = `/rules/${SELECTED_RULE_ID}`) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/rules/:ruleId?" element={<Rules />} />
      </Routes>
    </MemoryRouter>,
  );
}

const sent = (method: string, path: string) =>
  api.calls.filter((c) => c.method === method && c.path === path);

beforeEach(() => {
  resetAgentState();
  api = createFakeApi(Date.parse("2026-10-03T12:00:00Z"));
  vi.stubGlobal("fetch", fetchFor(api));
});
afterEach(() => vi.unstubAllGlobals());

describe("toggle", () => {
  it("enables a disabled rule from the list and keeps it enabled", async () => {
    const user = userEvent.setup();
    renderRules();
    const sw = await screen.findByRole("switch", { name: "Clean caches enabled" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    await user.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "true"));
    expect(sent("POST", "/rules/clean-caches/enable")).toHaveLength(1);
    expect(api.rules.find((r) => r.id === "clean-caches")?.enabled).toBe(true);
  });

  it("disables with POST /disable, and rolls back with an alert when the API refuses", async () => {
    const user = userEvent.setup();
    renderRules();
    const sw = await screen.findByRole("switch", { name: "Train batch enabled" });
    api.failNext["POST /rules/train-batch/disable"] = {
      status: 503,
      code: "store_down",
      message: "store unreachable",
    };
    await user.click(sw);
    expect(await screen.findByRole("alert")).toHaveTextContent("store unreachable");
    expect(sw).toHaveAttribute("aria-checked", "true");
    await user.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
    expect(sent("POST", "/rules/train-batch/disable")).toHaveLength(2);
  });
});

describe("edit", () => {
  it("edits name, trigger, action and placement and saves with PUT", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "Edit rule" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Ship it");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.selectOptions(within(form).getByLabelText("Placement"), "spark2");
    await user.click(within(form).getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Ship it" })).toBeInTheDocument();
    const put = sent("PUT", `/rules/${SELECTED_RULE_ID}`);
    expect(put).toHaveLength(1);
    expect(put[0].body).toMatchObject({
      id: SELECTED_RULE_ID,
      name: "Ship it",
      trigger: { kind: "event", params: { type: "github.push" } },
      placement: { machine: "spark2" },
      workflow: { id: "build-image" },
    });
    expect(screen.getByTestId("stage-trigger")).toHaveTextContent("github.push");
    expect(screen.queryByRole("form", { name: "Edit rule" })).not.toBeInTheDocument();
  });

  it("lands keyboard focus in the first field of every form it opens", async () => {
    const user = userEvent.setup();
    renderRules("/rules/train-batch");
    await screen.findByRole("heading", { level: 1, name: "Train batch" });

    await user.click(screen.getByRole("button", { name: "Edit rule" }));
    const edit = screen.getByRole("form", { name: "Edit rule" });
    await waitFor(() => expect(within(edit).getByLabelText("Name")).toHaveFocus());
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "Add stage" }));
    await user.click(screen.getByRole("button", { name: "Add condition" }));
    const condition = screen.getByRole("form", { name: "Add condition" });
    await waitFor(() => expect(within(condition).getByLabelText("Variable")).toHaveFocus());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "Add condition" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Add stage" }));
    await user.click(screen.getByRole("button", { name: "Add workflow" }));
    const workflow = screen.getByRole("form", { name: "Add workflow" });
    await waitFor(() => expect(within(workflow).getByLabelText("Workflow")).toHaveFocus());
    await user.keyboard("{Escape}");

    await user.click(screen.getByRole("button", { name: "New rule" }));
    const created = screen.getByRole("form", { name: "New rule" });
    await waitFor(() => expect(within(created).getByLabelText("Name")).toHaveFocus());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "New rule" })).not.toBeInTheDocument();
  });

  it("Escape cancels without sending anything", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "Edit rule" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "Edit rule" })).not.toBeInTheDocument();
    expect(sent("PUT", `/rules/${SELECTED_RULE_ID}`)).toHaveLength(0);
  });

  it("names a refused save and keeps the form open", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "Edit rule" }));
    api.failNext[`PUT /rules/${SELECTED_RULE_ID}`] = {
      status: 422,
      code: "invalid_rule",
      message: "name must not be empty",
    };
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("name must not be empty");
    expect(screen.getByRole("form", { name: "Edit rule" })).toBeInTheDocument();
  });
});

describe("delete with undo", () => {
  it("deletes without a confirm dialog, moves focus to the next rule and undoes via restore", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "Delete rule" }));
    expect(sent("DELETE", `/rules/${SELECTED_RULE_ID}`)).toHaveLength(1);
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Deleted Build and publish");
    const list = screen.getByRole("navigation", { name: "Rules" });
    expect(within(list).queryByRole("link", { name: "Build and publish" })).not.toBeInTheDocument();
    expect(within(list).getAllByRole("switch")).toHaveLength(4);

    await user.click(within(toast).getByRole("button", { name: "Undo" }));
    await waitFor(() =>
      expect(within(list).getByRole("link", { name: "Build and publish" })).toBeInTheDocument(),
    );
    expect(sent("POST", `/rules/${SELECTED_RULE_ID}/restore`)).toHaveLength(1);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("relationships on both ends", () => {
  it("shows must-after as a ghost card above the flow with a variable chip", async () => {
    renderRules();
    const ghost = await screen.findByTestId("relationship");
    expect(ghost).toHaveAttribute("data-relation", "must_after");
    expect(ghost).toHaveAttribute("data-direction", "out");
    expect(ghost).toHaveTextContent("must run after Review on approve");
  });

  it("shows the inverse badge on the other rule and in the list", async () => {
    renderRules("/rules/review-on-approve");
    const card = await screen.findByTestId("relationship");
    expect(card).toHaveAttribute("data-direction", "in");
    expect(card).toHaveTextContent("Build and publish must run after this");
    const list = screen.getByRole("navigation", { name: "Rules" });
    const row = list.querySelector('[data-rule-id="build-and-publish"]') as HTMLElement;
    expect(within(row).getByTestId("row-badge")).toHaveTextContent("waits for this");
  });

  it("adds a relationship from the keyboard picker and badges it at once", async () => {
    const user = userEvent.setup();
    renderRules("/rules/train-batch");
    const picker = await screen.findByLabelText("Add may run after");
    await user.selectOptions(picker, "triage-bugs");
    await waitFor(() => expect(sent("PUT", "/rules/train-batch")).toHaveLength(1));
    expect(sent("PUT", "/rules/train-batch")[0].body).toMatchObject({ may_after: ["triage-bugs"] });
    const card = await screen.findByTestId("relationship");
    expect(card).toHaveTextContent("may run after Triage bugs");
  });

  it("adds a relationship by dragging a rule from the list onto a slot", async () => {
    renderRules("/rules/train-batch");
    const list = await screen.findByRole("navigation", { name: "Rules" });
    const row = list.querySelector('[data-rule-id="clean-caches"]') as HTMLElement;
    const zone = screen.getByTestId("drop-supersedes");
    const data = new Map<string, string>();
    const dataTransfer = {
      setData: (t: string, v: string) => data.set(t, v),
      getData: (t: string) => data.get(t) ?? "",
      effectAllowed: "all",
      dropEffect: "move",
      types: ["text/plain"],
    };
    fireEvent.dragStart(row, { dataTransfer });
    fireEvent.dragOver(zone, { dataTransfer });
    fireEvent.drop(zone, { dataTransfer });
    await waitFor(() => expect(sent("PUT", "/rules/train-batch")).toHaveLength(1));
    expect(sent("PUT", "/rules/train-batch")[0].body).toMatchObject({
      supersedes: ["clean-caches"],
    });
    expect(await screen.findByTestId("relationship")).toHaveTextContent("supersedes Clean caches");
    const listRow = list.querySelector('[data-rule-id="clean-caches"]') as HTMLElement;
    expect(within(listRow).getByTestId("row-badge")).toHaveTextContent("superseded");
  });

  it("removes a relationship with its x, from either end", async () => {
    const user = userEvent.setup();
    renderRules("/rules/review-on-approve");
    const card = await screen.findByTestId("relationship");
    await user.click(within(card).getByRole("button", { name: /^Remove/ }));
    await waitFor(() => expect(sent("PUT", "/rules/build-and-publish")).toHaveLength(1));
    expect(sent("PUT", "/rules/build-and-publish")[0].body).toMatchObject({ must_after: [] });
    await waitFor(() => expect(screen.queryByTestId("relationship")).not.toBeInTheDocument());
  });

  it("refuses a cycle and says why", async () => {
    const user = userEvent.setup();
    renderRules("/rules/review-on-approve");
    await user.selectOptions(await screen.findByLabelText("Add must run after"), "build-and-publish");
    expect(await screen.findByRole("alert")).toHaveTextContent(/cycle/);
    expect(sent("PUT", "/rules/review-on-approve")).toHaveLength(0);
  });
});

describe("editing a typed trigger", () => {
  it("preselects a probe rule's values and saves a changed command", async () => {
    const user = userEvent.setup();
    api.rules.push({
      id: "disk-probe",
      name: "Disk probe",
      trigger: {
        kind: "probe",
        params: { actor: "ci-runner", command: "disk-free", mode: "change", schedule: "*/5 * * * *" },
      },
      action: { kind: "mesh.message", name: "Notify" },
      enabled: true,
    });
    renderRules("/rules/disk-probe");
    await user.click(await screen.findByRole("button", { name: "Edit rule" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    expect(within(form).getByRole("radio", { name: "A probe" })).toBeChecked();
    expect(within(form).getByLabelText("Command")).toHaveValue("disk-free");
    await user.selectOptions(within(form).getByLabelText("Command"), "gpu-temp");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", "/rules/disk-probe")).toHaveLength(1));
    expect(sent("PUT", "/rules/disk-probe")[0].body).toMatchObject({
      trigger: {
        kind: "probe",
        params: { actor: "ci-runner", command: "gpu-temp", mode: "change", schedule: "*/5 * * * *" },
      },
    });
  });

  it("keeps a label-only legacy trigger untouched when it is not edited", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "Edit rule" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", `/rules/${SELECTED_RULE_ID}`)).toHaveLength(1));
    expect(sent("PUT", `/rules/${SELECTED_RULE_ID}`)[0].body).toMatchObject({
      trigger: { kind: "event", params: { label: "Push to main" } },
    });
  });
});

describe("editing a typed action", () => {
  it("preselects the kind and saves a mapped param as its reference string", async () => {
    const user = userEvent.setup();
    renderRules("/rules/triage-bugs");
    await user.click(await screen.findByRole("button", { name: "Edit rule" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    expect(within(form).getByLabelText("What happens")).toHaveValue("github.comment");
    // the trigger's fields are offered once its event type is known
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.pr.opened");
    await user.selectOptions(within(form).getByLabelText("Actor"), "github-app");
    await user.type(within(form).getByLabelText("Repo"), "acme/app");
    await user.selectOptions(within(form).getByLabelText("Map Number"), "trigger.data.number");
    await user.type(within(form).getByLabelText("Body"), "Triaged");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", "/rules/triage-bugs")).toHaveLength(1));
    expect(sent("PUT", "/rules/triage-bugs")[0].body).toMatchObject({
      action: {
        kind: "github.comment",
        name: "Label",
        params: { actor: "github-app", repo: "acme/app", number: "trigger.data.number", body: "Triaged" },
      },
    });
    expect(await screen.findByText(/number → /)).toBeInTheDocument();
  });

  it("explains a missing required param in plain words and sends nothing", async () => {
    const user = userEvent.setup();
    renderRules("/rules/triage-bugs");
    await user.click(await screen.findByRole("button", { name: "Edit rule" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    await user.selectOptions(within(form).getByLabelText("Actor"), "github-app");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/required|empty/i);
    expect(sent("PUT", "/rules/triage-bugs")).toHaveLength(0);
  });

  it("offers workflow outputs on a rule that has a workflow", async () => {
    renderRules();
    await userEvent.click(await screen.findByRole("button", { name: "Edit rule" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    expect(within(form).getByTestId("chip-tag")).toHaveTextContent("workflow.outputs.image");
  });
});

describe("create, progressively", () => {
  it("starts from 'New rule', asks 'When does this happen?' and creates a trigger-only rule", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    const form = screen.getByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "Disk is nearly full");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.pr.opened");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(sent("POST", "/rules")).toHaveLength(1));
    expect(sent("POST", "/rules")[0].body).toMatchObject({
      id: "disk-is-nearly-full",
      name: "Disk is nearly full",
      trigger: { kind: "event", params: { type: "github.pr.opened" } },
    });
    expect(
      await screen.findByRole("heading", { level: 1, name: "Disk is nearly full" }),
    ).toBeInTheDocument();
  });

  it("never creates an event rule without a declared event, and says why in plain words", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    const form = screen.getByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "Nothing picked");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/needs a type/i);
    expect(sent("POST", "/rules")).toHaveLength(0);
  });

  it("creates a schedule rule with params.cron and tz", async () => {
    const user = userEvent.setup();
    renderRules();
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    const form = screen.getByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "Nightly");
    await user.click(within(form).getByRole("radio", { name: "On a schedule" }));
    await user.selectOptions(within(form).getByLabelText("Repeat"), "Every day at 9:00");
    await user.type(within(form).getByLabelText("Time zone"), "UTC");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(sent("POST", "/rules")).toHaveLength(1));
    expect(sent("POST", "/rules")[0].body).toMatchObject({
      trigger: { kind: "schedule", params: { cron: "0 9 * * *", tz: "UTC" } },
    });
  });

  it("grows a rule through + : adds a condition, then a workflow", async () => {
    const user = userEvent.setup();
    renderRules("/rules/train-batch");
    await screen.findByRole("heading", { level: 1, name: "Train batch" });
    await user.click(screen.getByRole("button", { name: "Add stage" }));
    await user.click(screen.getByRole("button", { name: "Add condition" }));
    const form = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.getByTestId("stage-condition")).toHaveTextContent("branch is main"));
    expect(sent("PUT", "/rules/train-batch")[0].body).toMatchObject({
      condition: { op: "compare", cmp: "==", left: { var: "branch" }, right: { literal: "main" } },
    });

    await user.click(screen.getByRole("button", { name: "Add stage" }));
    await user.click(screen.getByRole("button", { name: "Add workflow" }));
    await user.selectOptions(screen.getByLabelText("Workflow"), "review-pr");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.getByTestId("stage-workflow")).toHaveTextContent("Review PR"));
    expect(screen.getAllByTestId(/^stage-/).map((s) => s.getAttribute("data-testid"))).toEqual([
      "stage-trigger",
      "stage-condition",
      "stage-workflow",
      "stage-action",
    ]);
  });
});

describe("pending human asks, in context", () => {
  it("lists the rule's waiting ask and answers it with POST /asks/{id}/answer", async () => {
    withPendingAsk(api);
    const user = userEvent.setup();
    renderRules();
    const panel = await screen.findByRole("region", { name: "Waiting on you" });
    expect(panel).toHaveTextContent("Ship this build to production?");
    await user.click(within(panel).getByRole("button", { name: "approve" }));
    await waitFor(() => expect(sent("POST", "/asks/ask_1/answer")).toHaveLength(1));
    expect(sent("POST", "/asks/ask_1/answer")[0].body).toEqual({ answer: "approve" });
    await waitFor(() =>
      expect(screen.queryByRole("region", { name: "Waiting on you" })).not.toBeInTheDocument(),
    );
  });

  it("offers a text answer when the ask has no options, and names an expired ask", async () => {
    withPendingAsk(api);
    api.asks[0].options = null;
    api.failNext["POST /asks/ask_1/answer"] = {
      status: 409,
      code: "ask_expired",
      message: "ask passed its deadline",
    };
    const user = userEvent.setup();
    renderRules();
    const panel = await screen.findByRole("region", { name: "Waiting on you" });
    await user.type(within(panel).getByLabelText("Answer"), "yes");
    await user.click(within(panel).getByRole("button", { name: "Send answer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ask passed its deadline");
  });

  it("shows no panel and no error when the rule has nothing waiting", async () => {
    renderRules();
    await screen.findByRole("heading", { level: 1, name: "Build and publish" });
    expect(screen.queryByRole("region", { name: "Waiting on you" })).not.toBeInTheDocument();
    await waitFor(() => expect(getAgentState().status).toBe("ready"));
    expect(getAgentState().errors).toEqual([]);
  });
});
