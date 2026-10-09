import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAgentState } from "../../agent-state/store";
import { LIVE_DEBOUNCE_MS, setLiveSourceFactory } from "../../api/live";
import { SELECTED_RULE_ID } from "../../fixtures/rules-fixture";
import { createFakeApi, fetchFor, withActiveRuns, withPendingAsk, type FakeApi } from "../../rules/fake-api";
import { FakeEventSource } from "../../test/fakeEventSource";
import SimpleView from "./SimpleView";

/**
 * The Rules tab's editor scenarios (before the fold: src/rules/RulesEditor.test.tsx,
 * RulesLive.test.tsx, StopRuns.test.tsx and the board's own src/routes/Rules.test.tsx),
 * moved to where a rule is edited now: an entry point of the workflow it starts, in the
 * Simple view (spec c16, c29; plan t9). The board drove `/rules/<id>`; here every
 * fixture rule starts build-image, so each is one of its entry points, and `entry`
 * opens the one a scenario is about, as `/workflows?id=build-image&entry=<id>` does.
 */
let api: FakeApi;
type User = ReturnType<typeof userEvent.setup>;

const NOW = Date.parse("2026-10-03T12:00:00Z");

/** The rules fixture with every rule starting build-image (Build and publish already does). */
function foldedApi(): FakeApi {
  const fake = createFakeApi(NOW);
  for (const rule of fake.rules) rule.workflow ??= { id: "build-image", inputs: {} };
  return fake;
}

function renderEntry(entry: string = SELECTED_RULE_ID) {
  return render(
    <MemoryRouter>
      <SimpleView workflowId="build-image" entry={entry} />
    </MemoryRouter>,
  );
}

const sent = (method: string, path: string) => api.calls.filter((c) => c.method === method && c.path === path);
const entry = (name: string) => screen.getByRole("group", { name: `Entry point: ${name}` });
const findEntry = (name: string) => screen.findByRole("group", { name: `Entry point: ${name}` });
/** The open entry point named `name` (its body rendered). */
async function opened(name: string) {
  const card = await findEntry(name);
  await within(card).findByRole("button", { name: `Collapse ${name}` });
  return card;
}
const switchOf = (name: string) => within(entry(name)).getByRole("switch", { name: `${name} enabled` });

beforeEach(() => {
  resetAgentState();
  api = foldedApi();
  vi.stubGlobal("fetch", fetchFor(api));
});
afterEach(() => {
  setLiveSourceFactory(undefined);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("toggle (was RulesEditor › toggle)", () => {
  it("enables a disabled entry point from its switch and keeps it enabled", async () => {
    const user = userEvent.setup();
    renderEntry();
    await findEntry("Clean caches");
    const sw = switchOf("Clean caches");
    expect(sw).toHaveAttribute("aria-checked", "false");
    await user.click(sw);
    await waitFor(() => expect(switchOf("Clean caches")).toHaveAttribute("aria-checked", "true"));
    expect(sent("POST", "/rules/clean-caches/enable")).toHaveLength(1);
    expect(api.rules.find((r) => r.id === "clean-caches")?.enabled).toBe(true);
  });

  it("disables with POST /disable, and rolls back with an alert when the API refuses", async () => {
    const user = userEvent.setup();
    renderEntry();
    await findEntry("Train batch");
    api.failNext["POST /rules/train-batch/disable"] = { status: 503, code: "store_down", message: "store unreachable" };
    await user.click(switchOf("Train batch"));
    expect(await screen.findByRole("alert")).toHaveTextContent("store unreachable");
    expect(switchOf("Train batch")).toHaveAttribute("aria-checked", "true");
    await user.click(switchOf("Train batch"));
    await waitFor(() => expect(switchOf("Train batch")).toHaveAttribute("aria-checked", "false"));
    expect(sent("POST", "/rules/train-batch/disable")).toHaveLength(2);
  });
});

describe("edit (was RulesEditor › edit)", () => {
  it("edits name, trigger, action and placement and saves with PUT", async () => {
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "Edit Build and publish" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    const name = within(form).getByLabelText("Name");
    await user.clear(name);
    await user.type(name, "Ship it");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.selectOptions(within(form).getByLabelText("Placement"), "spark2");
    await user.click(within(form).getByRole("button", { name: "Save" }));

    const shipped = await findEntry("Ship it");
    const put = sent("PUT", `/rules/${SELECTED_RULE_ID}`);
    expect(put).toHaveLength(1);
    expect(put[0].body).toMatchObject({
      id: SELECTED_RULE_ID,
      name: "Ship it",
      trigger: { kind: "event", params: { type: "github.push" } },
      placement: { machine: "spark2" },
      workflow: { id: "build-image" },
    });
    expect(shipped).toHaveTextContent("github.push");
    expect(screen.queryByRole("form", { name: "Edit rule" })).not.toBeInTheDocument();
  });

  it("lands keyboard focus in the first field of every form it opens", async () => {
    const user = userEvent.setup();
    renderEntry("train-batch");
    const card = await opened("Train batch");

    await user.click(within(card).getByRole("button", { name: "Edit Train batch" }));
    const edit = screen.getByRole("form", { name: "Edit rule" });
    await waitFor(() => expect(within(edit).getByLabelText("Name")).toHaveFocus());
    await user.keyboard("{Escape}");

    await user.click(within(card).getByRole("button", { name: "Add condition" }));
    const condition = screen.getByRole("form", { name: "Add condition" });
    await waitFor(() => expect(within(condition).getByLabelText("Variable")).toHaveFocus());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "Add condition" })).not.toBeInTheDocument();

    // "New rule" for this workflow is + Entry point (the list's New rule: WorkflowsFolded.test).
    await user.click(screen.getByRole("button", { name: "Entry point" }));
    const created = screen.getByRole("form", { name: "New rule" });
    await waitFor(() => expect(within(created).getByLabelText("Name")).toHaveFocus());
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "New rule" })).not.toBeInTheDocument();
  });

  it("Escape cancels without sending anything", async () => {
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "Edit Build and publish" }));
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("form", { name: "Edit rule" })).not.toBeInTheDocument();
    expect(sent("PUT", `/rules/${SELECTED_RULE_ID}`)).toHaveLength(0);
  });

  it("names a refused save and keeps the form open", async () => {
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "Edit Build and publish" }));
    api.failNext[`PUT /rules/${SELECTED_RULE_ID}`] = { status: 422, code: "invalid_rule", message: "name must not be empty" };
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("name must not be empty");
    expect(screen.getByRole("form", { name: "Edit rule" })).toBeInTheDocument();
  });
});

describe("delete with undo (was RulesEditor › delete with undo)", () => {
  it("deletes without a confirm dialog, opens the next entry point and undoes via restore", async () => {
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "Delete Build and publish" }));
    expect(sent("DELETE", `/rules/${SELECTED_RULE_ID}`)).toHaveLength(1);
    const toast = await screen.findByRole("status");
    expect(toast).toHaveTextContent("Deleted Build and publish");
    expect(screen.queryByRole("group", { name: "Entry point: Build and publish" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("group", { name: /^Entry point: / })).toHaveLength(4);
    // Another entry point is open in its place.
    expect(screen.getAllByRole("button", { name: /^Collapse / })).toHaveLength(1);

    await user.click(within(toast).getByRole("button", { name: "Undo" }));
    expect(await findEntry("Build and publish")).toBeInTheDocument();
    expect(sent("POST", `/rules/${SELECTED_RULE_ID}/restore`)).toHaveLength(1);
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});

describe("relationships on both ends (was RulesEditor › relationships on both ends)", () => {
  it("shows must-after as a dashed card in the entry point's run order", async () => {
    renderEntry();
    const card = await opened("Build and publish");
    const ghost = within(card).getByTestId("relationship");
    expect(ghost).toHaveAttribute("data-relation", "must_after");
    expect(ghost).toHaveAttribute("data-direction", "out");
    expect(ghost).toHaveTextContent("must run after Review on approve");
  });

  it("shows the inverse card on the other entry point, which counts it in its run order", async () => {
    renderEntry("review-on-approve");
    const card = await opened("Review on approve");
    const rel = within(card).getByTestId("relationship");
    expect(rel).toHaveAttribute("data-direction", "in");
    expect(rel).toHaveTextContent("Build and publish must run after this");
    expect(within(card).getByRole("button", { name: /^Run order \(1\)/ })).toHaveAttribute("aria-expanded", "true");
  });

  it("adds a relationship from the keyboard picker and shows it at once", async () => {
    const user = userEvent.setup();
    renderEntry("train-batch");
    const card = await opened("Train batch");
    await user.click(within(card).getByRole("button", { name: /^Run order/ }));
    await user.selectOptions(within(card).getByLabelText("Add may run after"), "triage-bugs");
    await waitFor(() => expect(sent("PUT", "/rules/train-batch")).toHaveLength(1));
    expect(sent("PUT", "/rules/train-batch")[0].body).toMatchObject({ may_after: ["triage-bugs"] });
    expect(await within(card).findByTestId("relationship")).toHaveTextContent("may run after Triage bugs");
  });

  it("adds a relationship by dropping a rule onto a slot", async () => {
    const user = userEvent.setup();
    renderEntry("train-batch");
    const card = await opened("Train batch");
    await user.click(within(card).getByRole("button", { name: /^Run order/ }));
    const zone = within(card).getByTestId("drop-supersedes");
    const data = new Map<string, string>([["text/plain", "clean-caches"]]);
    const dataTransfer = {
      setData: (t: string, v: string) => data.set(t, v),
      getData: (t: string) => data.get(t) ?? "",
      effectAllowed: "all",
      dropEffect: "move",
      types: ["text/plain"],
    };
    fireEvent.dragOver(zone, { dataTransfer });
    fireEvent.drop(zone, { dataTransfer });
    await waitFor(() => expect(sent("PUT", "/rules/train-batch")).toHaveLength(1));
    expect(sent("PUT", "/rules/train-batch")[0].body).toMatchObject({ supersedes: ["clean-caches"] });
    expect(await within(card).findByTestId("relationship")).toHaveTextContent("supersedes Clean caches");
  });

  it("removes a relationship with its x, from either end", async () => {
    const user = userEvent.setup();
    renderEntry("review-on-approve");
    const card = await opened("Review on approve");
    await user.click(within(within(card).getByTestId("relationship")).getByRole("button", { name: /^Remove/ }));
    await waitFor(() => expect(sent("PUT", "/rules/build-and-publish")).toHaveLength(1));
    expect(sent("PUT", "/rules/build-and-publish")[0].body).toMatchObject({ must_after: [] });
    await waitFor(() => expect(within(card).queryByTestId("relationship")).not.toBeInTheDocument());
  });

  it("refuses a cycle and says why", async () => {
    const user = userEvent.setup();
    renderEntry("review-on-approve");
    const card = await opened("Review on approve");
    await user.selectOptions(within(card).getByLabelText("Add must run after"), "build-and-publish");
    expect(await screen.findByRole("alert")).toHaveTextContent(/cycle/);
    expect(sent("PUT", "/rules/review-on-approve")).toHaveLength(0);
  });
});

describe("editing a typed trigger (was RulesEditor › editing a typed trigger)", () => {
  it("preselects a probe rule's values and saves a changed command", async () => {
    const user = userEvent.setup();
    api.rules.push({
      id: "disk-probe",
      name: "Disk probe",
      trigger: { kind: "probe", params: { actor: "ci-runner", command: "disk-free", mode: "change", schedule: "*/5 * * * *" } },
      workflow: { id: "build-image", inputs: {} },
      action: { kind: "mesh.message", name: "Notify" },
      enabled: true,
    });
    renderEntry("disk-probe");
    const card = await opened("Disk probe");
    await user.click(within(card).getByRole("button", { name: "Edit Disk probe" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    expect(within(form).getByRole("radio", { name: "A probe" })).toBeChecked();
    expect(within(form).getByLabelText("Command")).toHaveValue("disk-free");
    await user.selectOptions(within(form).getByLabelText("Command"), "gpu-temp");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", "/rules/disk-probe")).toHaveLength(1));
    expect(sent("PUT", "/rules/disk-probe")[0].body).toMatchObject({
      trigger: { kind: "probe", params: { actor: "ci-runner", command: "gpu-temp", mode: "change", schedule: "*/5 * * * *" } },
    });
  });

  it("keeps a label-only legacy trigger untouched when it is not edited", async () => {
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "Edit Build and publish" }));
    await user.click(within(screen.getByRole("form", { name: "Edit rule" })).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", `/rules/${SELECTED_RULE_ID}`)).toHaveLength(1));
    expect(sent("PUT", `/rules/${SELECTED_RULE_ID}`)[0].body).toMatchObject({
      trigger: { kind: "event", params: { label: "Push to main" } },
    });
  });
});

describe("editing a typed action (was RulesEditor › editing a typed action)", () => {
  it("preselects the kind and saves a mapped param as its reference string", async () => {
    const user = userEvent.setup();
    // Triage bugs alone starts review-pr here, so its action is the workflow's "ends here".
    api = createFakeApi(NOW);
    api.rules.find((r) => r.id === "triage-bugs")!.workflow = { id: "review-pr", inputs: {} };
    vi.stubGlobal("fetch", fetchFor(api));
    render(
      <MemoryRouter>
        <SimpleView workflowId="review-pr" entry="triage-bugs" />
      </MemoryRouter>,
    );
    const card = await opened("Triage bugs");
    await user.click(within(card).getByRole("button", { name: "Edit Triage bugs" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    expect(within(form).getByLabelText("What happens")).toHaveValue("github.comment");
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
    await waitFor(() => expect(screen.getByRole("group", { name: "Ends here" })).toHaveTextContent("number → number"));
  });

  it("explains a missing required param in plain words and sends nothing", async () => {
    const user = userEvent.setup();
    renderEntry("triage-bugs");
    const card = await opened("Triage bugs");
    await user.click(within(card).getByRole("button", { name: "Edit Triage bugs" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    await user.selectOptions(within(form).getByLabelText("Actor"), "github-app");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/required|empty/i);
    expect(sent("PUT", "/rules/triage-bugs")).toHaveLength(0);
  });

  it("offers workflow outputs on a rule that has a workflow", async () => {
    renderEntry();
    const card = await opened("Build and publish");
    await userEvent.click(within(card).getByRole("button", { name: "Edit Build and publish" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    expect(within(form).getByTestId("chip-tag")).toHaveTextContent("workflow.outputs.image");
  });
});

describe("create, progressively (was RulesEditor › create, progressively)", () => {
  async function newEntryForm(user: User) {
    renderEntry();
    await findEntry("Build and publish");
    await user.click(screen.getByRole("button", { name: "Entry point" }));
    return screen.getByRole("form", { name: "New rule" });
  }

  it("starts from + Entry point, asks 'When does this happen?' and creates a trigger-only rule starting this workflow", async () => {
    const user = userEvent.setup();
    const form = await newEntryForm(user);
    expect(form).toHaveTextContent("When does this happen?");
    await user.type(within(form).getByLabelText("Name"), "Disk is nearly full");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.pr.opened");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(sent("POST", "/rules")).toHaveLength(1));
    expect(sent("POST", "/rules")[0].body).toMatchObject({
      id: "disk-is-nearly-full",
      name: "Disk is nearly full",
      trigger: { kind: "event", params: { type: "github.pr.opened" } },
      workflow: { id: "build-image", inputs: {} },
    });
    expect(await opened("Disk is nearly full")).toBeInTheDocument();
  });

  it("never creates an event rule without a declared event, and says why in plain words", async () => {
    const user = userEvent.setup();
    const form = await newEntryForm(user);
    await user.type(within(form).getByLabelText("Name"), "Nothing picked");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/needs a type/i);
    expect(sent("POST", "/rules")).toHaveLength(0);
  });

  it("creates a schedule rule with params.cron and tz", async () => {
    const user = userEvent.setup();
    const form = await newEntryForm(user);
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

  async function addConditionForm(user: User) {
    renderEntry("train-batch");
    const card = await opened("Train batch");
    await user.click(within(card).getByRole("button", { name: "Add condition" }));
    return { card, form: screen.getByRole("form", { name: "Add condition" }) };
  }

  it("an author check picks vars.trusted_authors and saves a var operand, not a copied list", async () => {
    const user = userEvent.setup();
    const { card, form } = await addConditionForm(user);
    await user.type(within(form).getByLabelText("Variable"), "trigger.data.author");
    await user.selectOptions(within(form).getByLabelText("Comparison"), "in");
    const picker = await within(form).findByLabelText("Allowed list");
    await waitFor(() => expect(within(picker).getByRole("option", { name: "vars.trusted_authors" })).toBeInTheDocument());
    expect(within(picker).queryByRole("option", { name: "vars.max_fixes" })).not.toBeInTheDocument();
    await user.selectOptions(picker, "trusted_authors");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(sent("PUT", "/rules/train-batch")).toHaveLength(1));
    const saved = sent("PUT", "/rules/train-batch")[0].body as { condition: unknown };
    expect(saved.condition).toEqual({ op: "in", value: { field: "data.author" }, items: { var: "trusted_authors" } });
    expect(JSON.stringify(saved.condition)).not.toContain("octocat");
    await waitFor(() => expect(within(card).getByRole("list", { name: "Only if all of" })).toHaveTextContent("vars.trusted_authors"));
  });

  it("a typed list is still a literal list", async () => {
    const user = userEvent.setup();
    const { form } = await addConditionForm(user);
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.selectOptions(within(form).getByLabelText("Comparison"), "in");
    await user.selectOptions(await within(form).findByLabelText("Allowed list"), "__typed__");
    await user.type(within(form).getByLabelText("Items"), "main, dev");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(sent("PUT", "/rules/train-batch")).toHaveLength(1));
    expect((sent("PUT", "/rules/train-batch")[0].body as { condition: unknown }).condition).toEqual({
      op: "in",
      value: { var: "branch" },
      items: { literal: ["main", "dev"] },
    });
  });

  it.each([
    ["variable_undefined", "does not exist yet"],
    ["variables_unsupported_nodes", "cannot read a variable"],
  ])("explains a refused save (%s) in plain words", async (code, words) => {
    const user = userEvent.setup();
    const { form } = await addConditionForm(user);
    api.failNext["PUT /rules/train-batch"] = { status: 422, code, message: "raw server text" };
    await user.type(within(form).getByLabelText("Variable"), "x");
    await user.selectOptions(within(form).getByLabelText("Comparison"), "in");
    const picker = await within(form).findByLabelText("Allowed list");
    await waitFor(() => expect(within(picker).getByRole("option", { name: "vars.trusted_authors" })).toBeInTheDocument());
    await user.selectOptions(picker, "trusted_authors");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(words);
    expect(alert).not.toHaveTextContent("raw server text");
  });

  it("grows an entry point through + : adds a condition (its workflow it already has)", async () => {
    const user = userEvent.setup();
    const { card, form } = await addConditionForm(user);
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    // The row reads as the board's "branch is main" did, in the Simple view's words: branch = "main".
    await waitFor(() => expect(within(card).getByRole("list", { name: "Only if all of" })).toHaveTextContent('branch="main"'));
    expect(sent("PUT", "/rules/train-batch")[0].body).toMatchObject({
      condition: { op: "compare", cmp: "==", left: { var: "branch" }, right: { literal: "main" } },
    });
    // The + grows again: a second term joins the first under "all of".
    await user.click(within(card).getByRole("button", { name: "Add condition" }));
    const again = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(again).getByLabelText("Variable"), "repo");
    await user.type(within(again).getByLabelText("Value"), "acme");
    await user.click(within(again).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(sent("PUT", "/rules/train-batch")).toHaveLength(2));
    expect((sent("PUT", "/rules/train-batch")[1].body as { condition: { op: string; args: unknown[] } }).condition)
      .toMatchObject({ op: "and", args: [{ left: { var: "branch" } }, { left: { var: "repo" } }] });
  });
});

describe("pending human asks, in context (was RulesEditor › pending human asks)", () => {
  it("lists the entry point's waiting ask and answers it with POST /asks/{id}/answer", async () => {
    withPendingAsk(api);
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    const panel = await within(card).findByRole("region", { name: "Waiting on you" });
    expect(panel).toHaveTextContent("Ship this build to production?");
    await user.click(within(panel).getByRole("button", { name: "approve" }));
    await waitFor(() => expect(sent("POST", "/asks/ask_1/answer")).toHaveLength(1));
    expect(sent("POST", "/asks/ask_1/answer")[0].body).toEqual({ answer: "approve" });
    await waitFor(() => expect(screen.queryByRole("region", { name: "Waiting on you" })).not.toBeInTheDocument());
  });

  it("offers a text answer when the ask has no options, and names an expired ask", async () => {
    withPendingAsk(api);
    api.asks[0].options = null;
    api.failNext["POST /asks/ask_1/answer"] = { status: 409, code: "ask_expired", message: "ask passed its deadline" };
    const user = userEvent.setup();
    renderEntry();
    const panel = await screen.findByRole("region", { name: "Waiting on you" });
    await user.type(within(panel).getByLabelText("Answer"), "yes");
    await user.click(within(panel).getByRole("button", { name: "Send answer" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("ask passed its deadline");
  });

  it("shows no panel and no error when the entry point has nothing waiting", async () => {
    renderEntry();
    await opened("Build and publish");
    await waitFor(() => expect(sent("GET", "/runs")).not.toHaveLength(0));
    expect(screen.queryByRole("region", { name: "Waiting on you" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});

describe("the board's entry point (was routes/Rules.test › Rules board)", () => {
  it("the entry point's (i) opens GET /rules/{id}/describe, lines verbatim, focus back on Escape (d19)", async () => {
    const lines = ["When push", "If verdict = approve", "and not (repo ∈ vars.excluded)", "Then publish"];
    const base = fetchFor(api);
    vi.stubGlobal("fetch", ((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).endsWith(`/rules/${SELECTED_RULE_ID}/describe`)
        ? Promise.resolve(new Response(JSON.stringify({ id: SELECTED_RULE_ID, kind: "rule", lines, entries: [] }), {
            status: 200,
            headers: { "content-type": "application/json" },
          }))
        : base(input, init)) as typeof fetch);
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    const about = within(card).getByRole("button", { name: "About Build and publish" });
    await user.click(about);
    const panel = within(card).getByRole("dialog", { name: "About Build and publish" });
    expect((await within(panel).findByTestId("about-lines")).textContent).toBe(lines.join("\n"));
    await user.keyboard("{Escape}");
    expect(about).toHaveFocus();
    expect(screen.queryByRole("region", { name: "In words" })).toBeNull();
  });

  it("draws the entry point as trigger → condition → workflow (inputs bound) → action", async () => {
    const user = userEvent.setup();
    // Only Build and publish starts build-image here: its action is the workflow's "ends here".
    api = createFakeApi(NOW);
    vi.stubGlobal("fetch", fetchFor(api));
    render(
      <MemoryRouter>
        <SimpleView workflowId="build-image" entry={SELECTED_RULE_ID} def={{ id: "build-image", name: "Build image", steps: [], edges: [] }} />
      </MemoryRouter>,
    );
    const card = await opened("Build and publish");
    expect(within(card).getByText("Push to main")).toBeInTheDocument();
    expect(within(card).getByRole("list", { name: "Only if all of" })).toHaveTextContent("verdict");
    expect(within(card).getByRole("list", { name: "Only if all of" })).toHaveTextContent("approve");
    // The workflow stage is the workflow itself: its steps column, and the inputs this rule binds.
    expect(screen.getByRole("region", { name: "Steps of this workflow" })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: /^2 inputs bound/ }));
    const bound = within(card).getByRole("list", { name: "Inputs bound" });
    expect(bound).toHaveTextContent("commit");
    expect(bound).toHaveTextContent("trigger.data.sha");
    // The action is the Then's "ends here", with its mapped params as the board drew them.
    expect(screen.getByRole("group", { name: "Ends here" })).toHaveTextContent("Publish");
    expect(screen.getByRole("group", { name: "Ends here" })).toHaveTextContent("image → tag");
    expect(within(card).getByRole("button", { name: "Add condition" })).toBeInTheDocument();
  });

  it("shows a relationship as a dashed card in the run order, not a stage", async () => {
    renderEntry();
    const card = await opened("Build and publish");
    const rel = within(card).getByTestId("relationship");
    expect(rel).toHaveTextContent("must run after Review on approve");
    expect(rel.closest(".fold-entry__order")).not.toBeNull();
  });

  it("shows where it evaluates (placement)", async () => {
    // Only Build and publish starts build-image here: its placement is the workflow's.
    api = createFakeApi(NOW);
    vi.stubGlobal("fetch", fetchFor(api));
    renderEntry();
    await opened("Build and publish");
    expect(screen.getByRole("group", { name: "Shared by every entry point" })).toHaveTextContent("evaluates on thor");
  });

  it("lists the last runs in context, a failure as a dot and a word", async () => {
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "History of Build and publish" }));
    const runs = await within(card).findByRole("list", { name: "Last runs" });
    await waitFor(() => expect(within(runs).getAllByRole("listitem")).toHaveLength(4));
    const failed = within(runs).getByText("failed").closest("li")!;
    expect(failed).toHaveAttribute("data-run-status", "failed");
    expect(failed.querySelector(".run-dot--failed")).not.toBeNull();
  });

  it("opens the first entry point when none is named", async () => {
    render(
      <MemoryRouter>
        <SimpleView workflowId="build-image" />
      </MemoryRouter>,
    );
    const first = (await screen.findAllByRole("group", { name: /^Entry point: / }))[0];
    expect(within(first).getByRole("button", { name: /^Collapse / })).toHaveAttribute("aria-expanded", "true");
  });
});

async function emit(collection: string, id: string) {
  act(() => FakeEventSource.latest().change(collection, id));
  await act(async () => {
    await new Promise((r) => setTimeout(r, LIVE_DEBOUNCE_MS + 30));
  });
}

describe("live updates (was RulesLive › Rules tab live updates, h61 / c80)", () => {
  beforeEach(() => {
    FakeEventSource.reset();
    setLiveSourceFactory(FakeEventSource.factory);
  });

  it("on its own, the Simple view subscribes to rules, runs, asks and rule decisions", async () => {
    renderEntry();
    await findEntry("Train batch");
    const url = new URL(FakeEventSource.latest().url, "http://x");
    expect(url.pathname).toBe("/api/events/stream");
    expect(url.searchParams.get("collections")?.split(",").sort()).toEqual(["asks", "rule_decisions", "rules", "runs"]);
  });

  it("an entry point toggled elsewhere shows here without a reload", async () => {
    renderEntry();
    await findEntry("Train batch");
    expect(switchOf("Train batch")).toHaveAttribute("aria-checked", "true");
    api.rules.find((r) => r.id === "train-batch")!.enabled = false; // another editor's write
    await emit("rules", "train-batch");
    await waitFor(() => expect(switchOf("Train batch")).toHaveAttribute("aria-checked", "false"));
  });

  it("a new ask on a run of this entry point appears when runs/asks change", async () => {
    renderEntry();
    await opened("Build and publish");
    expect(screen.queryByRole("region", { name: "Waiting on you" })).toBeNull();
    withPendingAsk(api);
    await emit("asks", "ask_1");
    expect(await screen.findByRole("region", { name: "Waiting on you" })).toHaveTextContent("Ship this build to production?");
  });
});

describe("last runs show the entry point's history (was RulesLive › h78 / c97)", () => {
  it("a superseded skip reads 'superseded by <rule name>', labelled skipped, newest first", async () => {
    api.decisions = [
      {
        rule_id: SELECTED_RULE_ID,
        event_id: "evt_7",
        reason: "superseded_by",
        by: ["review-on-approve"],
        message: "superseded by review-on-approve",
        at: new Date(api.now - 30 * 60_000).toISOString(),
        host: "spark",
      },
    ];
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "History of Build and publish" }));
    const runs = await within(card).findByRole("list", { name: "Last runs" });
    await waitFor(() => expect(within(runs).getAllByRole("listitem")).toHaveLength(5));
    const skip = within(runs).getByText("superseded by Review on approve").closest("li")!;
    expect(skip).toHaveAttribute("data-decision", "superseded_by");
    expect(within(skip).getByText(/skipped/)).toBeInTheDocument();
    // newest first: the 4m run, then the 30m skip
    expect(within(runs).getAllByRole("listitem")[1]).toBe(skip);
    expect(api.calls.some((c) => c.path === `/rules/${SELECTED_RULE_ID}/history`)).toBe(true);
  });

  it("a decision record arriving live is shown without a reload", async () => {
    FakeEventSource.reset();
    setLiveSourceFactory(FakeEventSource.factory);
    const user = userEvent.setup();
    renderEntry();
    const card = await opened("Build and publish");
    await user.click(within(card).getByRole("button", { name: "History of Build and publish" }));
    const runs = await within(card).findByRole("list", { name: "Last runs" });
    await waitFor(() => expect(within(runs).getAllByRole("listitem")).toHaveLength(4));
    api.decisions.push({
      rule_id: SELECTED_RULE_ID,
      event_id: "evt_8",
      reason: "superseded_by",
      by: ["review-on-approve"],
      message: "superseded by review-on-approve",
      at: new Date(api.now - 60_000).toISOString(),
      host: "thor",
    });
    await emit("rule_decisions", "d1");
    expect(await within(card).findByText("superseded by Review on approve")).toBeInTheDocument();
  });
});

describe("toggle in flight (was RulesLive › #7)", () => {
  it("a double click while the toggle is in flight sends one request and disables the switch", async () => {
    const real = fetchFor(api);
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (/\/rules\/train-batch\/(enable|disable)$/.test(String(input))) await gate;
      return real(input, init);
    }) as typeof fetch);
    renderEntry();
    await findEntry("Train batch");
    const sw = switchOf("Train batch");
    act(() => {
      sw.click();
      sw.click();
    });
    await waitFor(() => expect(switchOf("Train batch")).toHaveAttribute("aria-disabled", "true"));
    release();
    await waitFor(() => expect(switchOf("Train batch")).not.toHaveAttribute("aria-disabled"));
    expect(api.calls.filter((c) => /\/rules\/train-batch\/(enable|disable)$/.test(c.path))).toHaveLength(1);
  });
});

/** d17: disabling an entry point with runs still going asks 'Stop N current runs?' (was StopRuns.test). */
async function disableTrainBatch(user: User) {
  await findEntry("Train batch");
  const sw = switchOf("Train batch");
  await user.click(sw);
  await waitFor(() => expect(switchOf("Train batch")).toHaveAttribute("aria-checked", "false"));
  return switchOf("Train batch");
}

describe("stop current runs on disable (was StopRuns › stop current runs on disable)", () => {
  it("asks 'Stop N current runs?' without taking focus, and Approve stops them", async () => {
    withActiveRuns(api, "train-batch", 2);
    const user = userEvent.setup();
    renderEntry("train-batch");
    const sw = await disableTrainBatch(user);

    const notice = await screen.findByText(/Stop 2 current runs\?/);
    expect(notice).toHaveTextContent("Train batch is off. Stop 2 current runs?");
    expect(sw).toHaveFocus(); // non-modal: the toggle keeps focus
    expect(sent("POST", "/rules/train-batch/stop-runs")).toHaveLength(0); // never automatic

    await user.click(screen.getByRole("button", { name: /^Approve/ }));
    expect(await screen.findByText("Stopped 2 runs of Train batch.")).toBeInTheDocument();
    const stop = sent("POST", "/rules/train-batch/stop-runs");
    expect(stop).toHaveLength(1);
    expect(stop[0].body).toEqual({ apply: true });
    expect(api.activeRuns["train-batch"]).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(/Stopped 2 runs/)).not.toBeInTheDocument();
  });

  it("Keep running dismisses it and stops nothing", async () => {
    withActiveRuns(api, "train-batch", 1);
    const user = userEvent.setup();
    renderEntry("train-batch");
    await disableTrainBatch(user);
    expect(await screen.findByText(/Stop 1 current run\?/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep running" }));
    expect(screen.queryByText(/current run/)).not.toBeInTheDocument();
    expect(sent("POST", "/rules/train-batch/stop-runs")).toHaveLength(0);
    expect(api.activeRuns["train-batch"]).toHaveLength(1);
    expect(api.rules.find((r) => r.id === "train-batch")?.enabled).toBe(false);
  });

  it("is reachable and operable from the keyboard alone", async () => {
    withActiveRuns(api, "train-batch", 3);
    const user = userEvent.setup();
    renderEntry("train-batch");
    await findEntry("Train batch");
    switchOf("Train batch").focus();
    await user.keyboard(" ");
    await screen.findByText(/Stop 3 current runs\?/);
    screen.getByRole("button", { name: /^Approve/ }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("Stopped 3 runs of Train batch.")).toBeInTheDocument();
  });

  it("asks nothing when the disabled entry point has no runs going", async () => {
    const user = userEvent.setup();
    renderEntry("train-batch");
    await disableTrainBatch(user);
    await waitFor(() => expect(sent("POST", "/rules/train-batch/disable")).toHaveLength(1));
    expect(screen.queryByText(/current run/)).not.toBeInTheDocument();
  });

  it("a refused stop keeps the question open with the failure shown", async () => {
    withActiveRuns(api, "train-batch", 2);
    api.failNext["POST /rules/train-batch/stop-runs"] = { status: 409, code: "rule_enabled", message: "rule train-batch is enabled" };
    const user = userEvent.setup();
    renderEntry("train-batch");
    await disableTrainBatch(user);
    await user.click(await screen.findByRole("button", { name: /^Approve/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("rule train-batch is enabled");
    expect(screen.getByText(/Stop 2 current runs\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Approve/ })).toBeEnabled();
  });

  it("re-enabling the entry point withdraws the question", async () => {
    withActiveRuns(api, "train-batch", 2);
    const user = userEvent.setup();
    renderEntry("train-batch");
    const sw = await disableTrainBatch(user);
    await screen.findByText(/Stop 2 current runs\?/);
    await user.click(sw);
    await waitFor(() => expect(switchOf("Train batch")).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByText(/current runs/)).not.toBeInTheDocument();
  });
});

/** A fetch over the fake API that holds `stop-runs` requests until released. */
function holdStops(base: FakeApi) {
  const held: (() => void)[] = [];
  const inner = fetchFor(base);
  const fetchFn = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.includes("/stop-runs")) return inner(input, init);
    return new Promise<Response>((resolve) => held.push(() => resolve(inner(input, init))));
  }) as typeof fetch;
  return { fetchFn, releaseAll: () => held.splice(0).forEach((go) => go()), held };
}

describe("a stop in flight never clobbers a newer offer (was StopRuns)", () => {
  it("disabling another entry point while a stop is pending keeps the new question", async () => {
    withActiveRuns(api, "train-batch", 2);
    withActiveRuns(api, "review-on-approve", 1);
    const hold = holdStops(api);
    vi.stubGlobal("fetch", hold.fetchFn);
    const user = userEvent.setup();
    renderEntry("train-batch");
    await disableTrainBatch(user);
    await user.click(await screen.findByRole("button", { name: /^Approve/ }));
    await waitFor(() => expect(hold.held).toHaveLength(1));

    await user.click(switchOf("Review on approve"));
    expect(await screen.findByText(/Review on approve is off\. Stop 1 current run\?/)).toBeInTheDocument();

    hold.releaseAll();
    await waitFor(() => expect(api.activeRuns["train-batch"]).toEqual([]));
    await new Promise((r) => setTimeout(r, 0));
    expect(screen.getByText(/Review on approve is off\. Stop 1 current run\?/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Approve: stop 1 current run of Review on approve/ })).toBeEnabled();
    expect(screen.queryByText(/Stopped 2 runs/)).not.toBeInTheDocument();
  });

  it("a failed stop does not bring back an offer withdrawn by re-enabling", async () => {
    withActiveRuns(api, "train-batch", 2);
    api.failNext["POST /rules/train-batch/stop-runs"] = { status: 409, code: "rule_enabled", message: "rule train-batch is enabled" };
    const hold = holdStops(api);
    vi.stubGlobal("fetch", hold.fetchFn);
    const user = userEvent.setup();
    renderEntry("train-batch");
    const sw = await disableTrainBatch(user);
    await user.click(await screen.findByRole("button", { name: /^Approve/ }));
    await waitFor(() => expect(hold.held).toHaveLength(1));
    await user.click(sw); // re-enable: the question is withdrawn
    await waitFor(() => expect(switchOf("Train batch")).toHaveAttribute("aria-checked", "true"));
    expect(screen.queryByText(/current runs\?/)).not.toBeInTheDocument();

    hold.releaseAll();
    expect(await screen.findByRole("alert")).toHaveTextContent("rule train-batch is enabled");
    expect(screen.queryByText(/current runs\?/)).not.toBeInTheDocument();
  });
});
