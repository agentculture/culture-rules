import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAgentState } from "../../agent-state/store";
import type { Condition, Rule } from "../../api/types";
import { SELECTED_RULE_ID } from "../../fixtures/rules-fixture";
import { createFakeApi, withActiveRuns, type FakeApi } from "../../rules/fake-api";
import EntryEditors from "./EntryEditors";
import { FOLD_RULES, FOLD_WORKFLOWS, RUN_KEY } from "../simple/fixture";
import {
  concurrent, FOLD_NOW as NOW, defFor, editRunKey, entry, failure, findEntry, foldApi, openEntry, renderEditors, sent, thenGroup as then, use as useFake,
  testFeed, writes, type User,
} from "./entryHarness";

let api: FakeApi;
function use(fake: FakeApi) {
  api = fake;
  useFake(fake);
}

beforeEach(() => {
  resetAgentState();
  use(foldApi());
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("When: the workflow's entry points", () => {
  it("lists every rule that starts the workflow, continuations and disabled ones included", async () => {
    renderEditors();
    const view = await screen.findByRole("region", { name: "Entry points and Then" });
    expect(within(view).getByRole("heading", { level: 3, name: "When" })).toBeInTheDocument();
    // The view mounts before its rules load: wait for the entry points (one load lists them all).
    const names = (await within(view).findAllByRole("group", { name: /^Entry point: / }))
      .map((g) => g.getAttribute("aria-label"));
    expect(names).toEqual([
      "Entry point: Checks settled, not green",
      "Entry point: Trusted PR comment",
      "Entry point: Trusted review",
      "Entry point: Review asked for changes",
    ]);
    // the continuation that starts review-commit belongs to review-commit, not here
    expect(screen.queryByRole("group", { name: "Entry point: Review the fix" })).not.toBeInTheDocument();

    expect(within(entry("Checks settled, not green")).getByRole("switch", { name: "Checks settled, not green enabled" }))
      .toHaveAttribute("aria-checked", "true");
    const disabled = entry("Trusted review");
    expect(within(disabled).getByRole("switch", { name: "Trusted review enabled" })).toHaveAttribute("aria-checked", "false");
    expect(disabled).toHaveAttribute("data-enabled", "false");
    expect(disabled).toHaveTextContent("disabled");

    const continuation = entry("Review asked for changes");
    expect(continuation).toHaveTextContent("continuation");
    expect(continuation).toHaveTextContent("from review-commit");
    expect(entry("Trusted PR comment")).toHaveTextContent("github.comment.created");
  });

  it("opens an entry point: trigger, condition, placement and attempt counting", async () => {
    const user = userEvent.setup();
    renderEditors();
    // the first entry point opens by itself (openEntry is a no-op for it)
    const first = await openEntry(user, "Checks settled, not green");
    expect(within(first).getByRole("button", { name: "Collapse Checks settled, not green" }))
      .toHaveAttribute("aria-expanded", "true");
    expect(first).toHaveTextContent("event");
    expect(first).toHaveTextContent("github.pr.checks_settled");
    const rows = within(first).getByRole("list", { name: "Only if all of" });
    expect(within(rows).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "head_repo=base_repo",
      "draft=false",
      "repositoryinvars.fixer_repos",
      "conclusion≠\"success\"",
    ]);
    expect(within(first).getByRole("switch", { name: "Counts toward the attempt budget" }))
      .toHaveAttribute("aria-checked", "true");
    expect(first).toHaveTextContent("counts toward the 3 attempts");
    // every entry evaluates on spark2: said once, for the workflow
    expect(screen.getByRole("group", { name: "Shared by every entry point" })).toHaveTextContent("evaluates on spark2");
    expect(first).not.toHaveTextContent("spark2");
  });

  it("a continuation shows its predecessor apart from its own condition", async () => {
    const user = userEvent.setup();
    renderEditors();
    const refix = await openEntry(user, "Review asked for changes");
    expect(within(refix).getByRole("button", { name: "Collapse Review asked for changes" })).toBeInTheDocument();
    expect(within(refix).getByLabelText("Continues from")).toHaveValue("review-commit");
    const rows = within(refix).getByRole("list", { name: "Only if all of" });
    expect(within(rows).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "outputs.review=\"request_changes\"",
    ]);
    // one entry open at a time
    await openEntry(user, "Trusted PR comment");
    expect(within(entry("Review asked for changes")).getByRole("button", { name: "Expand Review asked for changes" }))
      .toHaveAttribute("aria-expanded", "false");
  });
});


describe("shared values once, differing ones per entry", () => {
  it("a value is shared only when every entry holds it identically; otherwise each entry shows its own", async () => {
    use(foldApi((rules) => {
      rules[1].placement = { machine: "thor" };
      rules[3].action = { ...rules[3].action, name: "Say the refix ended" };
    }));
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    // three on spark2, one on thor: no majority wins, every entry carries its own placement
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    expect(shared).toHaveTextContent("evaluates: differs per entry point");
    expect(shared).not.toHaveTextContent("spark2");
    for (const name of ["Trusted PR comment", "Trusted review", "Review asked for changes"]) {
      expect(entry(name)).toHaveTextContent("override");
    }
    expect(within(entry("Checks settled, not green")).getByRole("list", { name: "Overrides" })).toHaveTextContent("evaluates on spark2");
    const comment = await openEntry(user, "Trusted PR comment");
    expect(within(comment).getByRole("list", { name: "Overrides" })).toHaveTextContent("evaluates on thor");

    const ends = then("Ends here");
    expect(ends).toHaveTextContent("Differs per entry point");
    const listed = within(ends).getByRole("list", { name: "Overrides" });
    expect(within(listed).getAllByRole("listitem")).toHaveLength(4);
    expect(listed).toHaveTextContent("Review asked for changes: Say the refix ended");
    expect(listed).toHaveTextContent("Checks settled, not green: Comment when the chain ends here");
    // the run key is identical everywhere: shared, no overrides
    expect(within(then("Runs")).queryByRole("list", { name: "Overrides" })).not.toBeInTheDocument();
  });
});

describe("shared values once: the override count", () => {
  it("the shared panel says how many entry points override a differing value", async () => {
    use(foldApi((rules) => {
      rules[1].placement = { machine: "thor" };
    }));
    renderEditors();
    await findEntry("Checks settled, not green");
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    expect(shared).toHaveTextContent("evaluates: differs per entry point");
    expect(shared).toHaveTextContent("4 overrides");
  });
});

/**
 * What the Rules tab sent for each flow below, recorded from it (the board, src/routes/Rules.tsx,
 * last run as src/rules/RulesBoard.legacy.tsx) before it was deleted with the fold (t9). The
 * Simple view must send exactly these: the same rule document, through the same endpoint.
 */

const BUILD_AND_PUBLISH = {
  id: "build-and-publish",
  name: "Build and publish",
  trigger: { kind: "event", params: { label: "Push to main" } },
  condition: { op: "compare", cmp: "==", left: { var: "verdict" }, right: { literal: "approve" } },
  workflow: { id: "build-image", inputs: { commit: "trigger.data.sha", repo: "trigger.data.repo" } },
  action: { kind: "http.call", name: "Publish", params: { tag: "workflow.outputs.image" } },
  placement: { machine: "thor" },
  must_after: ["review-on-approve"],
  enabled: true,
};
const RULES_TAB = {
  editForm: [["PUT", "/rules/build-and-publish", {
    ...BUILD_AND_PUBLISH,
    name: "Ship it",
    trigger: { kind: "event", params: { type: "github.push" } },
    action: { kind: "noop", name: "PublishHold" },
    placement: { machine: "spark2" },
  }]],
  addCondition: [["PUT", "/rules/train-batch", {
    id: "train-batch",
    name: "Train batch",
    trigger: { kind: "schedule", params: { label: "Hourly" } },
    action: { kind: "code.run", name: "Train" },
    placement: { machine: "thor" },
    enabled: true,
    workflow: { id: "review-pr", inputs: {} },
    condition: { op: "compare", cmp: "==", left: { var: "branch" }, right: { literal: "main" } },
  }]],
  relate: [["PUT", "/rules/build-and-publish", { ...BUILD_AND_PUBLISH, may_after: ["train-batch"] }]],
  unrelate: [["PUT", "/rules/build-and-publish", { ...BUILD_AND_PUBLISH, must_after: [] }]],
  disable: [["POST", "/rules/build-and-publish/disable", undefined]],
};

/** Run a user flow in the Simple view; return its writes, as the Rules tab's recorded above. */
async function putBodies(
  rulesTab: unknown[][],
  ruleId: string,
  workflowId: string,
  flow: (user: User, scope: HTMLElement) => Promise<void>,
  setup?: (fake: FakeApi) => void,
): Promise<[unknown[][], unknown[][]]> {
  const fake = createFakeApi(NOW);
  setup?.(fake);
  use(fake);
  const user = userEvent.setup();
  const view = renderEditors({ workflowId });
  const name = fake.rules.find((r) => r.id === ruleId)!.name;
  await flow(user, await openEntry(user, name));
  await waitFor(() => expect(fake.calls.some((c) => c.method !== "GET")).toBe(true));
  await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
  const simple = fake.calls.filter((c) => c.method !== "GET").map((c) => [c.method, c.path, c.body]);
  view.unmount();
  return [rulesTab, simple];
}

describe("the same rule document the Rules tab saved", () => {
  it("the edit form: name, trigger, action and placement", async () => {
    const [rules, simple] = await putBodies(RULES_TAB.editForm, SELECTED_RULE_ID, "build-image", async (user, scope) => {
      const open = within(scope).queryByRole("button", { name: "Edit rule" })
        ?? within(scope).getByRole("button", { name: "Edit Build and publish" });
      await user.click(open);
      const form = screen.getByRole("form", { name: "Edit rule" });
      await user.clear(within(form).getByLabelText("Name"));
      await user.type(within(form).getByLabelText("Name"), "Ship it");
      await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
      await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
      await user.selectOptions(within(form).getByLabelText("What happens"), "noop");
      await user.type(within(form).getByLabelText("Action label"), "Hold");
      await user.selectOptions(within(form).getByLabelText("Placement"), "spark2");
      await user.click(within(form).getByRole("button", { name: "Save" }));
    });
    expect(rules).toHaveLength(1);
    expect(simple).toEqual(rules);
    expect(simple[0]).toEqual(["PUT", `/rules/${SELECTED_RULE_ID}`, expect.objectContaining({ name: "Ship it" })]);
  });

  it("adding a condition", async () => {
    const setup = (fake: FakeApi) => {
      fake.rules.find((r) => r.id === "train-batch")!.workflow = { id: "review-pr", inputs: {} };
    };
    const [rules, simple] = await putBodies(RULES_TAB.addCondition, "train-batch", "review-pr", async (user, scope) => {
      const stage = within(scope).queryByRole("button", { name: "Add stage" });
      if (stage) await user.click(stage);
      await user.click(screen.getByRole("button", { name: "Add condition" }));
      const form = screen.getByRole("form", { name: "Add condition" });
      await user.type(within(form).getByLabelText("Variable"), "branch");
      await user.type(within(form).getByLabelText("Value"), "main");
      await user.click(within(form).getByRole("button", { name: "Add" }));
    }, setup);
    expect(rules).toHaveLength(1);
    expect(simple).toEqual(rules);
  });

  it("a relationship", async () => {
    const [rules, simple] = await putBodies(RULES_TAB.relate, SELECTED_RULE_ID, "build-image", async (user, scope) => {
      await user.selectOptions(within(scope).getByLabelText("Add may run after"), "train-batch");
    });
    expect(rules).toHaveLength(1);
    expect(simple).toEqual(rules);
    expect((simple[0] as unknown[])[2]).toMatchObject({ may_after: ["train-batch"] });
  });

  it("removing a relationship", async () => {
    const [rules, simple] = await putBodies(RULES_TAB.unrelate, SELECTED_RULE_ID, "build-image", async (user, scope) => {
      await user.click(within(scope).getByRole("button", { name: "Remove: must run after Review on approve" }));
    });
    expect(rules).toHaveLength(1);
    expect(simple).toEqual(rules);
  });

  it("enable / disable", async () => {
    const [rules, simple] = await putBodies(RULES_TAB.disable, SELECTED_RULE_ID, "build-image", async (user) => {
      await user.click(screen.getAllByRole("switch", { name: "Build and publish enabled" })[0]);
    });
    expect(simple).toEqual(rules);
    expect(simple).toEqual([["POST", `/rules/${SELECTED_RULE_ID}/disable`, undefined]]);
  });
});


describe("one entry point, one rule", () => {
  it("renaming an entry point issues one PUT, for its rule only", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Edit Checks settled, not green" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "Checks went red");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    expect(await findEntry("Checks went red")).toBeInTheDocument();
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["PUT /rules/pr-fixer-checks"]);
    expect(writes()[0].body).toEqual({ ...FOLD_RULES[0], name: "Checks went red" });
  });

  it("disabling one entry point writes its rule only, and it stays listed and marked", async () => {
    const user = userEvent.setup();
    renderEditors();
    const comment = await findEntry("Trusted PR comment");
    const sw = within(comment).getByRole("switch", { name: "Trusted PR comment enabled" });
    await user.click(sw);
    await waitFor(() => expect(sw).toHaveAttribute("aria-checked", "false"));
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["POST /rules/pr-fixer-comment/disable"]);
    expect(entry("Trusted PR comment")).toHaveAttribute("data-enabled", "false");
    expect(entry("Trusted PR comment")).toHaveTextContent("disabled");
    expect(api.rules.filter((r) => r.enabled === false).map((r) => r.id)).toEqual(["pr-fixer-comment", "pr-fixer-review"]);
  });

  it("the attempt-budget switch writes that entry's rule only", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("switch", { name: "Counts toward the attempt budget" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(1));
    expect(writes()).toHaveLength(1);
    // validate.py: a rule outside the budget keeps its run key and may not set max_attempts
    expect(sent("PUT", "/rules/pr-fixer-checks")[0].body).toEqual({ ...FOLD_RULES[0], counts_toward_budget: false, max_attempts: null });
    await waitFor(() => expect(within(entry("Checks settled, not green")).getByRole("switch", { name: "Counts toward the attempt budget" }))
      .toHaveAttribute("aria-checked", "false"));
  });

  it("the attempt-budget switch cannot opt out a rule with no run key", async () => {
    const user = userEvent.setup();
    renderEditors({ workflowId: "publish-fix" });
    const any = await openEntry(user, "Any run failed");
    const sw = within(any).getByRole("switch", { name: "Counts toward the attempt budget" });
    expect(sw).toHaveAttribute("aria-disabled", "true");
    expect(any).toHaveTextContent("opting out needs a run key");
    await user.click(sw);
    expect(writes()).toHaveLength(0);
  });

  it("adds an entry point: a new rule that starts this workflow", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    await user.click(screen.getByRole("button", { name: "Entry point" }));
    const form = screen.getByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "PR opened");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.pr.opened");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    expect(await findEntry("PR opened")).toBeInTheDocument();
    const post = sent("POST", "/rules");
    expect(post).toHaveLength(1);
    expect(post[0].body).toMatchObject({ id: "pr-opened", workflow: { id: "pr-fix", inputs: {} } });
  });
});


describe("history, describe and stop runs per entry point", () => {
  it("history reads GET /rules/{id}/history", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "History of Checks settled, not green" }));
    const history = await within(first).findByRole("list", { name: "Last runs" });
    await waitFor(() => expect(history).toHaveTextContent("No runs yet"));
    expect(sent("GET", "/rules/pr-fixer-checks/history")).toHaveLength(1);
  });

  it("history lists runs and skips", async () => {
    const user = userEvent.setup();
    api.decisions.push({
      rule_id: "pr-fixer-comment", event_id: "e1", reason: "superseded_by", by: ["pr-fixer-checks"],
      message: "", at: new Date(NOW - 5 * 60_000).toISOString(), host: "spark",
    });
    renderEditors();
    const comment = await openEntry(user, "Trusted PR comment");
    await user.click(within(comment).getByRole("button", { name: "History of Trusted PR comment" }));
    const history = await within(comment).findByRole("list", { name: "Last runs" });
    await waitFor(() => expect(history).toHaveTextContent("superseded by Checks settled, not green"));
  });

  it("describe reads GET /rules/{id}/describe", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "About Checks settled, not green" }));
    const panel = within(first).getByRole("dialog", { name: "About Checks settled, not green" });
    await waitFor(() => expect(panel).toHaveTextContent("When github.pr.checks_settled"));
    expect(sent("GET", "/rules/pr-fixer-checks/describe")).toHaveLength(1);
  });

  it("disabling with runs going offers to stop them, through POST /rules/{id}/stop-runs", async () => {
    withActiveRuns(api, "pr-fixer-checks", 2);
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("switch", { name: "Checks settled, not green enabled" }));
    expect(await screen.findByText(/Stop 2 current runs\?/, {}, { timeout: 800 })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Approve/ }));
    expect(await screen.findByText("Stopped 2 runs of Checks settled, not green.")).toBeInTheDocument();
    expect(sent("POST", "/rules/pr-fixer-checks/stop-runs")).toEqual([
      { method: "POST", path: "/rules/pr-fixer-checks/stop-runs", body: { apply: true } },
    ]);
  });
});

describe("a continuation's predecessor", () => {
  it("rewrites exactly the data.workflow_id compare, and never offers its own workflow or none", async () => {
    const user = userEvent.setup();
    renderEditors();
    const refix = await openEntry(user, "Review asked for changes");
    const picker = within(refix).getByLabelText("Continues from");
    const options = within(picker).getAllByRole("option").map((o) => (o as HTMLOptionElement).value);
    expect(options).toEqual(["review-commit", "publish-fix"]);
    await user.selectOptions(picker, "publish-fix");
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-refix")).toHaveLength(1));
    expect(writes()).toHaveLength(1);
    const body = sent("PUT", "/rules/pr-fixer-refix")[0].body as Rule;
    expect(body).toEqual({
      ...FOLD_RULES[3],
      condition: {
        op: "and",
        args: [
          { op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: "publish-fix" } },
          { op: "compare", cmp: "==", left: { field: "data.outputs.review" }, right: { literal: "request_changes" } },
        ],
      },
    });
  });
});


describe("deleting entry points", () => {
  it("deleting the last entry point of a stepless workflow offers to delete the workflow too", async () => {
    const user = userEvent.setup();
    renderEditors({ workflowId: "review-commit", def: FOLD_WORKFLOWS[1] });
    const only = await findEntry("Review the fix");
    await user.click(within(only).getByRole("button", { name: "Delete Review the fix" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Entry point: Review the fix" })).not.toBeInTheDocument());
    expect(sent("DELETE", "/rules/pr-fixer-review-commit")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "Delete the workflow Review the fix too" }));
    await waitFor(() => expect(sent("DELETE", "/workflows/review-commit")).toHaveLength(1));
    expect(await screen.findByText("Deleted the workflow Review the fix.")).toBeInTheDocument();
  });

  it("never offers to delete a workflow with steps, or one that still has entry points; undo restores the rule", async () => {
    const user = userEvent.setup();
    renderEditors({ def: FOLD_WORKFLOWS[0] });
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Delete Checks settled, not green" }));
    expect(await screen.findByText("Deleted Checks settled, not green")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete the workflow/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(await findEntry("Checks settled, not green")).toBeInTheDocument();
    expect(sent("POST", "/rules/pr-fixer-checks/restore")).toHaveLength(1);
  });

  it("never offers to delete a workflow whose last entry point went while it has steps", async () => {
    const user = userEvent.setup();
    const withSteps = { ...FOLD_WORKFLOWS[1], steps: [{ id: "look", name: "Look", kind: "ai" }] } as typeof FOLD_WORKFLOWS[1];
    renderEditors({ workflowId: "review-commit", def: withSteps });
    const only = await findEntry("Review the fix");
    await user.click(within(only).getByRole("button", { name: "Delete Review the fix" }));
    expect(await screen.findByText("Deleted Review the fix")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("group", { name: "Entry point: Review the fix" })).not.toBeInTheDocument());
    expect(screen.queryByRole("button", { name: /Delete the workflow/ })).not.toBeInTheDocument();
    expect(sent("DELETE", "/workflows/review-commit")).toHaveLength(0);
  });
});


describe("review follow-up: conditions", () => {
  it("an entry's existing condition terms can be removed and added to, one PUT each", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Remove condition draft = false" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(1));
    const guard = (FOLD_RULES[0].condition as { args: Condition[] }).args;
    expect((sent("PUT", "/rules/pr-fixer-checks")[0].body as Rule).condition).toEqual({ op: "and", args: [guard[0], guard[2], guard[3]] });

    await waitFor(() => expect(within(entry("Checks settled, not green")).getAllByRole("listitem").length).toBeGreaterThan(0));
    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "Add condition" }));
    const form = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(2));
    expect((sent("PUT", "/rules/pr-fixer-checks")[1].body as Rule).condition).toEqual({
      op: "and",
      args: [guard[0], guard[2], guard[3], { op: "compare", cmp: "==", left: { var: "branch" }, right: { literal: "main" } }],
    });
    expect(writes().every((c) => c.path === "/rules/pr-fixer-checks")).toBe(true);
  });

  it("a continuation's own terms are editable; its data.workflow_id term is never a removable row", async () => {
    const user = userEvent.setup();
    renderEditors();
    const refix = await openEntry(user, "Review asked for changes");
    expect(within(refix).queryByRole("button", { name: /Remove condition workflow_id/ })).not.toBeInTheDocument();
    await user.click(within(refix).getByRole("button", { name: 'Remove condition outputs.review = "request_changes"' }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-refix")).toHaveLength(1));
    expect((sent("PUT", "/rules/pr-fixer-refix")[0].body as Rule).condition).toEqual({
      op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: "review-commit" },
    });
    // still linked from review-commit
    await waitFor(() => expect(within(entry("Review asked for changes")).getByLabelText("Continues from")).toHaveValue("review-commit"));
  });

  it("a shared condition is edited once for every entry point and keeps each continuation's predecessor term (c32)", async () => {
    use(foldApi((rules) => {
      const publish = rules.find((r) => r.id === "pr-fixer-publish")!;
      rules.splice(rules.findIndex((r) => r.id === "any-failure"), 1, { ...structuredClone(publish), id: "publish-2", name: "Publish again" });
    }));
    const user = userEvent.setup();
    renderEditors({ workflowId: "publish-fix" });
    await findEntry("Publish when approved");
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    const rows = within(shared).getByRole("list", { name: "Every entry point only if all of" });
    expect(within(rows).getAllByRole("listitem").map((li) => li.textContent)).toEqual(['outputs.review="approve"']);

    await user.click(within(shared).getByRole("button", { name: "Add a condition for every entry point" }));
    const form = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(writes()).toHaveLength(2));
    for (const put of writes()) {
      const args = ((put.body as Rule).condition as { args: Condition[] }).args;
      expect(args[0]).toEqual({ op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: "review-commit" } });
      expect(args).toHaveLength(3);
    }

    await waitFor(() => expect(within(screen.getByRole("group", { name: "Shared by every entry point" }))
      .getAllByRole("button", { name: /^Remove condition/ })).toHaveLength(2));
    await user.click(within(screen.getByRole("group", { name: "Shared by every entry point" }))
      .getByRole("button", { name: 'Remove condition outputs.review = "approve"' }));
    await waitFor(() => expect(writes()).toHaveLength(4));
    for (const put of writes().slice(2)) {
      const args = ((put.body as Rule).condition as { args: Condition[] }).args;
      expect(args).toEqual([
        { op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: "review-commit" } },
        { op: "compare", cmp: "==", left: { var: "branch" }, right: { literal: "main" } },
      ]);
    }
  });
});


describe("review follow-up: focus returns", () => {
  it("to the opener after cancel or save", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    const edit = within(first).getByRole("button", { name: "Edit Checks settled, not green" });
    await user.click(edit);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(edit).toHaveFocus());

    const runs = within(then("Runs")).getByRole("button", { name: "Edit runs" });
    await user.click(runs);
    await user.click(within(screen.getByRole("form", { name: "Runs" })).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(runs).toHaveFocus());
    await user.click(runs);
    await user.click(within(screen.getByRole("form", { name: "Runs" })).getByRole("button", { name: "Save for every entry point" }));
    await waitFor(() => expect(runs).toHaveFocus());

  });

  it("the restored entry takes focus after undo", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "Delete Checks settled, not green" }));
    await user.click(await screen.findByRole("button", { name: "Undo" }));
    await waitFor(() => expect(within(entry("Checks settled, not green")).getByRole("button", { name: "Collapse Checks settled, not green" })).toHaveFocus(), { timeout: 800 });
  });

  it("the Undo button takes focus when an entry point is deleted", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "Delete Checks settled, not green" }));
    const undo = await screen.findByRole("button", { name: "Undo" });
    await waitFor(() => expect(undo).toHaveFocus(), { timeout: 800 });
  });
});


describe("full-pass review follow-up", () => {
  it("from the mixed state, Placement, Ends here and On failure write nothing until a value is picked", async () => {
    use(foldApi((rules) => {
      rules[1].placement = { machine: "thor" };
      rules[3].action = { ...rules[3].action, name: "Other end" };
      rules[2].on_failure = null;
    }));
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");

    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    await user.click(within(shared).getByRole("button", { name: /evaluates: differs per entry point/ }));
    const place = screen.getByRole("form", { name: "Placement" });
    expect(within(place).getByLabelText("Evaluates on")).toHaveDisplayValue("Differs per entry point");
    await user.click(within(place).getByRole("button", { name: "Save for every entry point" }));

    await user.click(within(then("Ends here")).getByRole("button", { name: "Edit ends here" }));
    const ends = screen.getByRole("form", { name: "Ends here" });
    expect(ends).toHaveTextContent("Differs per entry point");
    await user.click(within(ends).getByRole("button", { name: "Save for every entry point" }));

    await user.click(within(then("On failure")).getByRole("button", { name: "Edit on failure" }));
    const fail = screen.getByRole("form", { name: "On failure" });
    expect(fail).toHaveTextContent("Differs per entry point");
    await user.click(within(fail).getByRole("button", { name: "Save for every entry point" }));
    expect(writes()).toHaveLength(0);

    // an explicit pick does write
    await user.click(within(screen.getByRole("group", { name: "Shared by every entry point" })).getByRole("button", { name: /evaluates/ }));
    await user.selectOptions(screen.getByLabelText("Evaluates on"), "Anywhere");
    await user.click(screen.getByRole("button", { name: "Save for every entry point" }));
    await waitFor(() => expect(writes()).toHaveLength(4));
    for (const put of writes()) expect((put.body as Rule).placement).toBeNull();
  });

  it("an empty failure action is not invented: an untouched save writes nothing", async () => {
    const user = userEvent.setup();
    renderEditors({ workflowId: "publish-fix" });
    await findEntry("Publish when approved");
    await user.click(within(then("On failure")).getByRole("button", { name: "Edit on failure" }));
    await user.click(within(screen.getByRole("form", { name: "On failure" })).getByRole("button", { name: "Save for every entry point" }));
    expect(writes()).toHaveLength(0);
  });

  it("refuses a run key the server would refuse, and junk in the budget", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    await user.click(within(then("Runs")).getByRole("button", { name: "Edit runs" }));
    const form = screen.getByRole("form", { name: "Runs" });
    await user.clear(within(form).getByLabelText("Run key"));
    await user.type(within(form).getByLabelText("Run key"), "pr:{{vars.x}");
    await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("only {trigger.<path>} placeholders");
    await user.clear(within(form).getByLabelText("Run key"));
    await user.type(within(form).getByLabelText("Run key"), RUN_KEY.replaceAll("{", "{{"));
    await user.clear(within(form).getByLabelText("Attempts per key"));
    await user.type(within(form).getByLabelText("Attempts per key"), "lots");
    await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("whole number");
    expect(writes()).toHaveLength(0);
  });

  it("condition results read as rows; a term already there is not added twice, and a no-op is not reported as saved", async () => {
    use(foldApi((rules) => {
      const publish = rules.find((r) => r.id === "pr-fixer-publish")!;
      rules.splice(rules.findIndex((r) => r.id === "any-failure"), 1, { ...structuredClone(publish), id: "publish-2", name: "Publish again" });
    }));
    const user = userEvent.setup();
    renderEditors({ workflowId: "publish-fix" });
    await findEntry("Publish again");
    // Publish again gains the term meanwhile, so it is skipped; applying to it as it is now changes nothing
    const other = api.rules.find((r) => r.id === "publish-2")!;
    (other.condition as { args: Condition[] }).args.push({ op: "compare", cmp: "==", left: { var: "branch" }, right: { literal: "main" } });
    Object.assign(other, { updated_at: "2026-10-09T13:00:00Z" });
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    await user.click(within(shared).getByRole("button", { name: "Add a condition for every entry point" }));
    const form = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    const results = await screen.findByRole("region", { name: "Save results" });
    expect(results).toHaveAttribute("aria-live", "polite");
    const row = await within(results).findByText("Publish again");
    await waitFor(() => expect(row.closest("li")).toHaveAttribute("data-status", "skipped-changed"));
    expect(row.closest("li")).toHaveTextContent('workflow_id = "review-commit"');
    expect(row.closest("li")).not.toHaveTextContent('"op"');
    await user.click(within(results).getByRole("button", { name: "Apply to Publish again as it is now" }));
    await waitFor(() => expect(row.closest("li")).toHaveAttribute("data-status", "unchanged"));
    expect(row.closest("li")).toHaveTextContent("already so");
    expect(sent("PUT", "/rules/publish-2")).toHaveLength(0);
  });

  it("a per-entry duplicate condition term is not written", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Add condition" }));
    const form = screen.getByRole("form", { name: "Add condition" });
    // trigger.data.draft == false is already there as a field compare; add a var term twice instead
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(1));
    await waitFor(() => expect(within(entry("Checks settled, not green")).getByRole("list", { name: "Only if all of" })).toHaveTextContent("vars.branch"));
    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "Add condition" }));
    const again = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(again).getByLabelText("Variable"), "branch");
    await user.type(within(again).getByLabelText("Value"), "main");
    await user.click(within(again).getByRole("button", { name: "Add" }));
    await waitFor(() => expect(screen.queryByRole("form", { name: "Add condition" })).not.toBeInTheDocument());
    expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(1);
  });

  it("focus returns after removing a condition row; + Entry point says it is expanded", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Remove condition draft = false" }));
    expect(within(entry("Checks settled, not green")).getByRole("button", { name: "Add condition" })).toHaveFocus();
    const add = screen.getByRole("button", { name: "Entry point" });
    expect(add).toHaveAttribute("aria-expanded", "false");
    expect(add).not.toHaveAttribute("aria-pressed");
    await user.click(add);
    expect(add).toHaveAttribute("aria-expanded", "true");
  });

  it("a different workflow starts fresh: its own first entry open, no earlier baselines", async () => {
    api.failNext["PUT /rules/pr-fixer-comment"] = failure;
    const user = userEvent.setup();
    const view = renderEditors({ workflowId: "pr-fix" });
    await editRunKey(user, "pr:{trigger.data.number}");
    await screen.findByRole("region", { name: "Save results" });
    view.rerender(<MemoryRouter><EntryEditors workflow={defFor("publish-fix")} /></MemoryRouter>);
    const first = await findEntry("Publish when approved");
    expect(within(first).getByRole("button", { name: "Collapse Publish when approved" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Save results" })).not.toBeInTheDocument();
  });

  // The same flow, with the first entry point still opening on its own.
  it("a different workflow shows its own first entry open", async () => {
    const view = renderEditors({ workflowId: "pr-fix" });
    await findEntry("Checks settled, not green");
    view.rerender(<MemoryRouter><EntryEditors workflow={defFor("publish-fix")} /></MemoryRouter>);
    const first = await findEntry("Publish when approved");
    expect(within(first).getByRole("button", { name: "Collapse Publish when approved" })).toBeInTheDocument();
  });
});

describe("re-review: unchanged after the re-read, delete only an unused workflow", () => {
  it("a rule whose snapshot already holds the value but changed meanwhile is skipped and flagged, with Apply", async () => {
    use(foldApi((rules) => { rules[1].placement = { machine: "thor" }; }));
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Trusted PR comment");
    Object.assign(api.rules[1], { name: "Trusted PR comment (renamed)", updated_at: "2026-10-09T13:00:00Z" });
    await user.click(within(screen.getByRole("group", { name: "Shared by every entry point" })).getByRole("button", { name: /evaluates/ }));
    await user.selectOptions(screen.getByLabelText("Evaluates on"), "thor");
    await user.click(screen.getByRole("button", { name: "Save for every entry point" }));
    const results = await screen.findByRole("region", { name: "Save results" });
    const row = await within(results).findByText("Trusted PR comment");
    await waitFor(() => expect(row.closest("li")).toHaveAttribute("data-status", "skipped-changed"));
    expect(within(results).getByRole("button", { name: "Apply to Trusted PR comment as it is now" })).toBeInTheDocument();
    expect(sent("PUT", "/rules/pr-fixer-comment")).toHaveLength(0);
    expect(sent("GET", "/rules/pr-fixer-comment")).toHaveLength(1);
  });

  it("Delete the workflow too re-reads the rules and keeps a workflow a rule still uses", async () => {
    const user = userEvent.setup();
    renderEditors({ workflowId: "review-commit", def: FOLD_WORKFLOWS[1] });
    const only = await findEntry("Review the fix");
    await user.click(within(only).getByRole("button", { name: "Delete Review the fix" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: "Entry point: Review the fix" })).not.toBeInTheDocument());
    // another editor points a rule at review-commit meanwhile
    api.rules.push({ ...structuredClone(FOLD_RULES[7]), id: "late", name: "Late", workflow: { id: "review-commit" } });
    await user.click(screen.getByRole("button", { name: "Delete the workflow Review the fix too" }));
    expect(await screen.findByText(/Kept the workflow Review the fix: Late still uses it/)).toBeInTheDocument();
    expect(sent("DELETE", "/workflows/review-commit")).toHaveLength(0);
  });
});

describe("fold-fixA 2: per-entry direct writes re-read first; a form freezes per form", () => {
  /**
   * Another writer changes rules on the server. No feed is handed in here, so the change is not
   * refreshed into the view: the write's own re-read is what catches it.
   */
  function elsewhere(ids: string[], change: Record<string, unknown>) {
    for (const id of ids) Object.assign(api.rules.find((r) => r.id === id)!, structuredClone(change), { updated_at: "2026-10-09T14:00:00Z" });
  }
  const newer = { ...FOLD_RULES[0].action, name: "Someone else's end" };

  it("an untouched Edit save after a concurrent action change does not PUT; it says so and reloads on request", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Edit Checks settled, not green" }));
    elsewhere(["pr-fixer-checks"], { action: newer });
    await user.click(within(screen.getByRole("form", { name: "Edit rule" })).getByRole("button", { name: "Save" }));
    const alert = await within(entry("Checks settled, not green")).findByRole("alert");
    expect(alert).toHaveTextContent("changed since you opened it");
    expect(alert).toHaveTextContent("action");
    expect(sent("GET", "/rules/pr-fixer-checks")).toHaveLength(1);
    expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(0);
    expect(api.rules[0].action).toEqual(newer);

    await user.click(within(alert).getByRole("button", { name: "Reload the form from the stored rule" }));
    const form = screen.getByRole("form", { name: "Edit rule" });
    expect(within(form).getByLabelText("Action label")).toHaveValue("Someone else's end");
    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "Checks went red");
    await user.click(within(form).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(1));
    expect(sent("PUT", "/rules/pr-fixer-checks")[0].body).toMatchObject({ name: "Checks went red", action: newer });
  });

  it("removing a condition term, adding one and relating re-read first and never overwrite a newer rule", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    elsewhere(["pr-fixer-checks"], { max_attempts: 8 });

    await user.click(within(first).getByRole("button", { name: "Remove condition draft = false" }));
    expect(await within(entry("Checks settled, not green")).findByRole("alert")).toHaveTextContent("changed since you opened it");

    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "Add condition" }));
    const form = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));

    const order = within(entry("Checks settled, not green")).queryByLabelText("Add may run after");
    if (!order) await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: /Run order/ }));
    await user.selectOptions(within(entry("Checks settled, not green")).getByLabelText("Add may run after"), "pr-fixer-comment");
    await waitFor(() => expect(sent("GET", "/rules/pr-fixer-checks").length).toBeGreaterThanOrEqual(3));
    expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(0);
    expect((api.rules[0] as Rule & { max_attempts?: number }).max_attempts).toBe(8);
  });

  it("the Add condition form keeps the rule it opened on: a refreshed condition change is a conflict, not a PUT", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Add condition" }));
    elsewhere(["pr-fixer-checks"], { condition: { op: "compare", cmp: "==", left: { field: "data.draft" }, right: { literal: false } } });
    const form = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));
    expect(await within(entry("Checks settled, not green")).findByRole("alert")).toHaveTextContent("changed since you opened it");
    expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(0);
    expect(api.rules[0].condition).toEqual({ op: "compare", cmp: "==", left: { field: "data.draft" }, right: { literal: false } });
  });

  it("switching straight from Ends here to Runs shows the budget as it is now", async () => {
    const feed = testFeed();
    const user = userEvent.setup();
    renderEditors({ live: feed });
    await findEntry("Checks settled, not green");
    await user.click(within(then("Ends here")).getByRole("button", { name: "Edit ends here" }));
    await concurrent(feed, ["pr-fixer-checks", "pr-fixer-comment", "pr-fixer-review", "pr-fixer-refix"], { max_attempts: 9 }, "2026-10-09T14:00:00Z");
    await user.click(within(then("Runs")).getByRole("button", { name: "Edit runs" }));
    expect(within(screen.getByRole("form", { name: "Runs" })).getByLabelText("Attempts per key")).toHaveValue("9");
  });

  it("switching straight between an entry's override forms shows the rule as it is now", async () => {
    const feed = testFeed();
    const user = userEvent.setup();
    renderEditors({ live: feed });
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "On failure for Checks settled, not green" }));
    await concurrent(feed, ["pr-fixer-checks"], { max_attempts: 9 }, "2026-10-09T14:00:00Z");
    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "Runs for Checks settled, not green" }));
    expect(within(screen.getByRole("form", { name: "Runs for Checks settled, not green" })).getByLabelText("Attempts per key")).toHaveValue("9");
  });
});

describe("creating rules once the Rules tab is gone", () => {
  it("+ Entry point sends the Rules tab's create body, with this workflow preset", async () => {
    const fill = async (user: User) => {
      const form = screen.getByRole("form", { name: "New rule" });
      await user.type(within(form).getByLabelText("Name"), "PR opened");
      await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
      await user.selectOptions(within(form).getByLabelText("Event"), "github.pr.opened");
      await user.click(within(form).getByRole("button", { name: "Create rule" }));
    };
    // What the Rules tab's New rule sent for this form (recorded before the tab was deleted, t9).
    const fromTab = {
      id: "pr-opened",
      name: "PR opened",
      trigger: { kind: "event", params: { type: "github.pr.opened" } },
      action: { kind: "mesh.message", name: "Notify" },
      enabled: true,
    };

    use(createFakeApi(NOW));
    const user = userEvent.setup();
    renderEditors({ workflowId: "build-image" });
    await findEntry("Build and publish");
    await user.click(screen.getByRole("button", { name: "Entry point" }));
    await fill(user);
    await waitFor(() => expect(sent("POST", "/rules")).toHaveLength(1));
    expect(sent("POST", "/rules")[0].body).toEqual({ ...fromTab, workflow: { id: "build-image", inputs: {} } });
  });
});

// Simple opened the entry point `?entry=<rule>` asked for, and followed a new one without
// remounting; Detailed's entry editors do the same (t23).
describe("fold-fixA: the view follows the asked-for entry", () => {
  it("opens the newly asked entry point without remounting", async () => {
    api.failNext["PUT /rules/pr-fixer-comment"] = failure;
    const user = userEvent.setup();
    const view = renderEditors({ entry: "pr-fixer-checks" });
    await findEntry("Checks settled, not green");
    await editRunKey(user, "pr:{trigger.data.number}");
    await screen.findByRole("region", { name: "Save results" });
    view.rerender(<MemoryRouter><EntryEditors workflow={defFor("pr-fix")} entry="pr-fixer-comment" /></MemoryRouter>);
    await waitFor(() => expect(within(entry("Trusted PR comment")).getByRole("button", { name: "Collapse Trusted PR comment" })).toBeInTheDocument());
    expect(within(entry("Checks settled, not green")).getByRole("button", { name: "Expand Checks settled, not green" })).toBeInTheDocument();
    // same instance: the save results are still there
    expect(screen.getByRole("region", { name: "Save results" })).toBeInTheDocument();
  });
});
