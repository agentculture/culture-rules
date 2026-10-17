import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAgentState } from "../../agent-state/store";
import type { Condition, Rule } from "../../api/types";
import { type FakeApi } from "../../rules/fake-api";
import { FOLD_RULES, ON_FAILURE, RUN_KEY } from "../simple/fixture";
import {
  concurrent, editRunKey, entry, failure, findEntry, foldApi, renderEditors, sent, testFeed, thenGroup as then, use as useFake,
  writes,
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

describe("Then: continues into, ends here, on failure, runs", () => {
  it("shows the chain onward as read-only links and the workflow's shared Then values", async () => {
    renderEditors();
    await findEntry("Checks settled, not green");
    const onward = then("Continues into");
    const link = within(onward).getByRole("link", { name: /Review the fix/ });
    expect(link).toHaveAttribute("href", "/workflows?id=review-commit");
    expect(onward).toHaveTextContent("Kept on review-commit as its entry point.");
    expect(within(onward).queryByRole("button")).not.toBeInTheDocument();

    expect(then("Ends here")).toHaveTextContent("Comment when the chain ends here");
    expect(then("Ends here")).toHaveTextContent("Only when the chain ends here.");
    expect(then("On failure")).toHaveTextContent("Hand back with the run link");
    expect(then("Runs")).toHaveTextContent(RUN_KEY);
    expect(then("Runs")).toHaveTextContent("3 attempts per key");
    expect(then("Runs")).toHaveTextContent("counted by 4 of 4 entry points");
  });

  it("a workflow reached from any workflow says so; the end of a chain continues into nothing", async () => {
    renderEditors({ workflowId: "publish-fix" });
    await findEntry("Any run failed");
    expect(entry("Any run failed")).toHaveTextContent("from any workflow");
    expect(within(entry("Any run failed")).queryByLabelText("Continues from")).not.toBeInTheDocument();
    expect(then("Continues into")).toHaveTextContent("Nothing continues from here.");
  });
});


describe("a shared edit fans out, rule by rule", () => {
  it("shows a partial failure per rule with its old value and a retry; the others saved", async () => {
    api.failNext["PUT /rules/pr-fixer-comment"] = failure;
    const user = userEvent.setup();
    renderEditors();
    await editRunKey(user, "pr:{trigger.data.number}");

    const results = await screen.findByRole("region", { name: "Save results" });
    await waitFor(() => expect(within(results).getAllByRole("listitem")).toHaveLength(4));
    const row = (name: string) => within(results).getByText(name).closest("li")!;
    expect(row("Checks settled, not green")).toHaveAttribute("data-status", "saved");
    expect(row("Trusted review")).toHaveAttribute("data-status", "saved");
    expect(row("Review asked for changes")).toHaveAttribute("data-status", "saved");
    expect(row("Trusted PR comment")).toHaveAttribute("data-status", "failed");
    expect(row("Trusted PR comment")).toHaveTextContent("not saved");
    expect(row("Trusted PR comment")).toHaveTextContent("store unreachable");
    expect(row("Trusted PR comment")).toHaveTextContent(`keeps ${RUN_KEY}`);
    expect(sent("PUT", "/rules/pr-fixer-comment")).toHaveLength(1);
    expect(api.rules.map((r) => (r as Rule & { concurrency_key?: string }).concurrency_key).slice(0, 4))
      .toEqual(["pr:{trigger.data.number}", RUN_KEY, "pr:{trigger.data.number}", "pr:{trigger.data.number}"]);

    // the failed rule stays an override with its old value
    await waitFor(() => expect(within(then("Runs")).getByRole("list", { name: "Overrides" }))
      .toHaveTextContent(`Trusted PR comment: ${RUN_KEY}`));
    expect(then("Runs")).toHaveTextContent("pr:{trigger.data.number}");
    expect(entry("Trusted PR comment")).toHaveTextContent("override");

    await user.click(within(results).getByRole("button", { name: "Retry Trusted PR comment" }));
    await waitFor(() => expect(row("Trusted PR comment")).toHaveAttribute("data-status", "saved"));
    expect(sent("PUT", "/rules/pr-fixer-comment")).toHaveLength(2);
    await waitFor(() => expect(within(then("Runs")).queryByRole("list", { name: "Overrides" })).not.toBeInTheDocument());
  });

  it("skips and flags a rule changed since it was shown; never overwrites it", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    // someone else edits Trusted review meanwhile
    Object.assign(api.rules[2], { max_attempts: 5, updated_at: "2026-10-09T12:30:00Z" });
    await editRunKey(user, "pr:{trigger.data.number}");
    const results = await screen.findByRole("region", { name: "Save results" });
    await waitFor(() => expect(within(results).getAllByRole("listitem")).toHaveLength(4));
    const row = within(results).getByText("Trusted review").closest("li")!;
    expect(row).toHaveAttribute("data-status", "skipped-changed");
    expect(row).toHaveTextContent("changed since you opened it");
    expect(sent("GET", "/rules/pr-fixer-review")).toHaveLength(1);
    expect(sent("PUT", "/rules/pr-fixer-review")).toHaveLength(0);
    expect((api.rules[2] as Rule & { concurrency_key?: string }).concurrency_key).toBe(RUN_KEY);
  });

  it("edits the chain-end action for every entry point with the action picker", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    await user.click(within(then("Ends here")).getByRole("button", { name: "Edit ends here" }));
    const form = screen.getByRole("form", { name: "Ends here" });
    await user.clear(within(form).getByLabelText("Action label"));
    await user.type(within(form).getByLabelText("Action label"), "Say where it stopped");
    await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-refix")).toHaveLength(1));
    const puts = writes();
    expect(puts.map((c) => c.path)).toEqual([
      "/rules/pr-fixer-checks", "/rules/pr-fixer-comment", "/rules/pr-fixer-review", "/rules/pr-fixer-refix",
    ]);
    for (const put of puts) expect((put.body as Rule).action.name).toBe("Say where it stopped");
    // a fanned-out edit never touches a continuation's predecessor term
    expect((puts[3].body as Rule).condition).toEqual(FOLD_RULES[3].condition);
  });

  it("removes the failure action from every entry point", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    expect(then("On failure")).toHaveTextContent(ON_FAILURE.name!);
    await user.click(within(then("On failure")).getByRole("button", { name: "Edit on failure" }));
    const form = screen.getByRole("form", { name: "On failure" });
    await user.click(within(form).getByRole("button", { name: "Remove for every entry point" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-refix")).toHaveLength(1));
    for (const put of writes()) expect((put.body as Rule).on_failure).toBeNull();
    await waitFor(() => expect(then("On failure")).toHaveTextContent("Nothing runs on failure."));
  });

  it("sets the shared placement for every entry point", async () => {
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    await user.click(within(shared).getByRole("button", { name: /evaluates on spark2/ }));
    await user.selectOptions(screen.getByLabelText("Evaluates on"), "thor");
    await user.click(screen.getByRole("button", { name: "Save for every entry point" }));
    await waitFor(() => expect(writes().filter((c) => c.method === "PUT")).toHaveLength(4));
    for (const put of writes()) expect((put.body as Rule).placement).toEqual({ machine: "thor" });
  });
});


const KEY_OF = (id: string) => (api.rules.find((r) => r.id === id) as Rule & { concurrency_key?: string }).concurrency_key;

describe("review follow-up: shared values, budgets and run edits", () => {
  it("after a partial failure, the failed rule is the override with its old value, even when it came first", async () => {
    api.failNext["PUT /rules/pr-fixer-checks"] = failure;
    const user = userEvent.setup();
    renderEditors();
    await editRunKey(user, "pr:{trigger.data.number}");
    await screen.findByRole("region", { name: "Save results" });
    await waitFor(() => expect(within(then("Runs")).getByRole("list", { name: "Overrides" }))
      .toHaveTextContent(`Checks settled, not green: ${RUN_KEY}`));
    const overrides = within(within(then("Runs")).getByRole("list", { name: "Overrides" })).getAllByRole("listitem");
    expect(overrides).toHaveLength(1);
    expect(then("Runs")).toHaveTextContent("pr:{trigger.data.number}");
    expect(within(entry("Checks settled, not green")).getByRole("list", { name: "Overrides" })).toHaveTextContent(`run key: ${RUN_KEY}`);
    expect(entry("Trusted PR comment")).not.toHaveTextContent("override");
  });

  it("editing the run key sends only the run key, never an untouched budget", async () => {
    use(foldApi((rules) => { (rules[1] as Rule & { max_attempts?: number }).max_attempts = 5; }));
    const user = userEvent.setup();
    renderEditors();
    await editRunKey(user, "pr:{trigger.data.number}");
    await waitFor(() => expect(writes().filter((c) => c.method === "PUT")).toHaveLength(4));
    const comment = sent("PUT", "/rules/pr-fixer-comment")[0].body as Rule & { max_attempts?: number };
    expect(comment.max_attempts).toBe(5);
    expect(KEY_OF("pr-fixer-comment")).toBe("pr:{trigger.data.number}");
  });

  it("refuses a budget on a rule outside the attempt budget, and says why", async () => {
    const user = userEvent.setup();
    renderEditors({ workflowId: "review-commit" });
    await findEntry("Review the fix");
    await user.click(within(then("Runs")).getByRole("button", { name: "Edit runs" }));
    const form = screen.getByRole("form", { name: "Runs" });
    await user.type(within(form).getByLabelText("Attempts per key"), "3");
    await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
    expect(within(form).getByRole("alert")).toHaveTextContent("does not count toward the attempt budget");
    expect(writes()).toHaveLength(0);
  });

  it("an entry's own failure action, run key and budget are editable for that entry alone", async () => {
    const user = userEvent.setup();
    renderEditors();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Runs for Checks settled, not green" }));
    const runs = screen.getByRole("form", { name: "Runs for Checks settled, not green" });
    await user.clear(within(runs).getByLabelText("Attempts per key"));
    await user.type(within(runs).getByLabelText("Attempts per key"), "2");
    await user.click(within(runs).getByRole("button", { name: "Save for this entry point" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(1));
    expect(writes()).toHaveLength(1);
    expect(sent("PUT", "/rules/pr-fixer-checks")[0].body).toEqual({ ...FOLD_RULES[0], max_attempts: 2 });
    // now an override of the budget, shown as such
    await waitFor(() => expect(within(then("Runs")).getByRole("list", { name: "Overrides" })).toHaveTextContent("Checks settled, not green: 2 attempts per key"));

    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "On failure for Checks settled, not green" }));
    const fail = screen.getByRole("form", { name: "On failure for Checks settled, not green" });
    await user.click(within(fail).getByRole("button", { name: "Remove for this entry point" }));
    await waitFor(() => expect(sent("PUT", "/rules/pr-fixer-checks")).toHaveLength(2));
    expect((sent("PUT", "/rules/pr-fixer-checks")[1].body as Rule).on_failure).toBeNull();
    expect(writes()).toHaveLength(2);
  });
});


describe("re-review follow-up", () => {
  const publishTwice = (rules: Rule[]) => {
    const publish = rules.find((r) => r.id === "pr-fixer-publish")!;
    rules.splice(rules.findIndex((r) => r.id === "any-failure"), 1, { ...structuredClone(publish), id: "publish-2", name: "Publish again" });
  };
  const fromTerm = (id: string) => ({ op: "compare", cmp: "==", left: { field: "data.workflow_id" }, right: { literal: id } });
  const branchMain = { op: "compare", cmp: "==", left: { var: "branch" }, right: { literal: "main" } };

  it("Apply to it as it is now adds the condition term to that rule's current condition, keeping its current predecessor", async () => {
    use(foldApi(publishTwice));
    const user = userEvent.setup();
    renderEditors({ workflowId: "publish-fix" });
    await findEntry("Publish again");
    // meanwhile someone repoints Publish again at pr-fix
    const other = api.rules.find((r) => r.id === "publish-2")!;
    (other.condition as { args: Condition[] }).args[0] = fromTerm("pr-fix") as Condition;
    Object.assign(other, { updated_at: "2026-10-09T13:00:00Z" });

    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    await user.click(within(shared).getByRole("button", { name: "Add a condition for every entry point" }));
    const form = screen.getByRole("form", { name: "Add condition" });
    await user.type(within(form).getByLabelText("Variable"), "branch");
    await user.type(within(form).getByLabelText("Value"), "main");
    await user.click(within(form).getByRole("button", { name: "Add" }));

    const results = await screen.findByRole("region", { name: "Save results" });
    await waitFor(() => expect(within(results).getByText("Publish again").closest("li")).toHaveAttribute("data-status", "skipped-changed"));
    expect(sent("PUT", "/rules/publish-2")).toHaveLength(0);
    await user.click(within(results).getByRole("button", { name: "Apply to Publish again as it is now" }));
    await waitFor(() => expect(sent("PUT", "/rules/publish-2")).toHaveLength(1));
    expect((sent("PUT", "/rules/publish-2")[0].body as Rule).condition).toEqual({
      op: "and",
      args: [fromTerm("pr-fix"), { op: "compare", cmp: "==", left: { field: "data.outputs.review" }, right: { literal: "approve" } }, branchMain],
    });
  });

  it("removing a shared condition term removes it from each rule's own condition", async () => {
    use(foldApi(publishTwice));
    const user = userEvent.setup();
    renderEditors({ workflowId: "publish-fix" });
    await findEntry("Publish again");
    const other = api.rules.find((r) => r.id === "publish-2")!;
    (other.condition as { args: Condition[] }).args[0] = fromTerm("pr-fix") as Condition;
    Object.assign(other, { updated_at: "2026-10-09T13:00:00Z" });
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    await user.click(within(shared).getByRole("button", { name: 'Remove condition outputs.review = "approve"' }));
    const results = await screen.findByRole("region", { name: "Save results" });
    await user.click(await within(results).findByRole("button", { name: "Apply to Publish again as it is now" }));
    await waitFor(() => expect(sent("PUT", "/rules/publish-2")).toHaveLength(1));
    expect((sent("PUT", "/rules/publish-2")[0].body as Rule).condition).toEqual(fromTerm("pr-fix"));
    expect((sent("PUT", "/rules/pr-fixer-publish")[0].body as Rule).condition).toEqual(fromTerm("review-commit"));
  });


  it("differing run keys and budgets can be cleared for every entry point; untouched mixed fields send nothing", async () => {
    use(foldApi((rules) => {
      (rules[1] as Rule & { max_attempts?: number }).max_attempts = 5;
      (rules[2] as Rule & { concurrency_key?: string }).concurrency_key = "other:{trigger.data.number}";
    }));
    const user = userEvent.setup();
    renderEditors();
    await findEntry("Checks settled, not green");
    await user.click(within(then("Runs")).getByRole("button", { name: "Edit runs" }));
    let form = screen.getByRole("form", { name: "Runs" });
    // both fields differ: untouched, the save sends nothing
    await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
    expect(writes()).toHaveLength(0);

    await user.click(within(then("Runs")).getByRole("button", { name: "Edit runs" }));
    form = screen.getByRole("form", { name: "Runs" });
    expect(within(form).getByLabelText("Attempts per key")).toHaveAttribute("placeholder", "Differs per entry point");
    await user.click(within(form).getByRole("button", { name: "No limit for every entry point" }));
    await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
    await waitFor(() => expect(writes()).toHaveLength(4));
    for (const put of writes()) {
      const body = put.body as Rule & { max_attempts?: number | null; concurrency_key?: string };
      expect(body.max_attempts).toBeNull();
      expect(body.concurrency_key).toBe(put.path === "/rules/pr-fixer-review" ? "other:{trigger.data.number}" : RUN_KEY);
    }
  });
});


/**
 * fold-fixA (c27): a rule changed elsewhere is refreshed into the view through the live feed while
 * a form is open; the open form keeps the snapshot it was opened on.
 */
describe("fold-fixA: an open form keeps the snapshot it was opened on (c27)", () => {
  const PR_FIX = ["pr-fixer-checks", "pr-fixer-comment", "pr-fixer-review", "pr-fixer-refix"];

  it("an untouched Ends here save after a concurrent action edit writes nothing over it", async () => {
    const feed = testFeed();
    const user = userEvent.setup();
    renderEditors({ live: feed });
    await findEntry("Checks settled, not green");
    await user.click(within(then("Ends here")).getByRole("button", { name: "Edit ends here" }));
    // someone else changes the chain-end action on every entry: still shared, but newer
    const newer = { ...FOLD_RULES[1].action, name: "Someone else's end" };
    await concurrent(feed, PR_FIX, { action: newer });
    await waitFor(() => expect(then("Ends here").querySelector(".fold-then__value")).toHaveTextContent("Someone else's end"));
    await user.click(within(screen.getByRole("form", { name: "Ends here" })).getByRole("button", { name: "Save for every entry point" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(writes()).toHaveLength(0);
    for (const id of PR_FIX) expect(api.rules.find((r) => r.id === id)!.action).toEqual(newer);
  });

  it("a changed Ends here save skips the rule edited meanwhile instead of overwriting it", async () => {
    const feed = testFeed();
    const user = userEvent.setup();
    renderEditors({ live: feed });
    await findEntry("Checks settled, not green");
    await user.click(within(then("Ends here")).getByRole("button", { name: "Edit ends here" }));
    const form = screen.getByRole("form", { name: "Ends here" });
    await user.clear(within(form).getByLabelText("Action label"));
    await user.type(within(form).getByLabelText("Action label"), "Mine");
    const newer = { ...FOLD_RULES[1].action, name: "Someone else's end" };
    await concurrent(feed, ["pr-fixer-comment"], { action: newer });
    await user.click(within(screen.getByRole("form", { name: "Ends here" })).getByRole("button", { name: "Save for every entry point" }));
    const results = await screen.findByRole("region", { name: "Save results" });
    await waitFor(() => expect(within(results).getByText("Trusted PR comment").closest("li")).toHaveAttribute("data-status", "skipped-changed"));
    expect(sent("PUT", "/rules/pr-fixer-comment")).toHaveLength(0);
    expect(api.rules.find((r) => r.id === "pr-fixer-comment")!.action).toEqual(newer);
  });

  it("an untouched Runs save after a concurrent budget edit restores nothing", async () => {
    const feed = testFeed();
    const user = userEvent.setup();
    renderEditors({ live: feed });
    await findEntry("Checks settled, not green");
    await user.click(within(then("Runs")).getByRole("button", { name: "Edit runs" }));
    // every entry's budget becomes 7 meanwhile: still shared, but newer
    await concurrent(feed, PR_FIX, { max_attempts: 7 });
    await waitFor(() => expect(then("Runs")).toHaveTextContent("7 attempts per key"));
    await user.click(within(screen.getByRole("form", { name: "Runs" })).getByRole("button", { name: "Save for every entry point" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(writes()).toHaveLength(0);
    for (const id of PR_FIX) expect((api.rules.find((r) => r.id === id) as Rule & { max_attempts?: number }).max_attempts).toBe(7);
  });

  it("a form opened in the mixed state stays mixed when a refresh makes the value shared: no invented noop", async () => {
    use(foldApi((rules) => { rules[3].action = { ...rules[3].action, name: "Other end" }; }));
    const feed = testFeed();
    const user = userEvent.setup();
    renderEditors({ live: feed });
    await findEntry("Checks settled, not green");
    await user.click(within(then("Ends here")).getByRole("button", { name: "Edit ends here" }));
    await concurrent(feed, ["pr-fixer-refix"], { action: FOLD_RULES[0].action });
    // the card now shows the shared value; the open form keeps what it was opened on (mixed)
    await waitFor(() => expect(then("Ends here").querySelector(".fold-then__value")).toHaveTextContent("Comment when the chain ends here"));
    expect(screen.getByRole("form", { name: "Ends here" })).toHaveTextContent("Differs per entry point");
    await user.click(within(screen.getByRole("form", { name: "Ends here" })).getByRole("button", { name: "Save for every entry point" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(writes()).toHaveLength(0);
  });

  it("an entry's own Runs form keeps its snapshot too", async () => {
    const feed = testFeed();
    const user = userEvent.setup();
    renderEditors({ live: feed });
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Runs for Checks settled, not green" }));
    await concurrent(feed, ["pr-fixer-checks"], { max_attempts: 9 });
    await user.click(within(screen.getByRole("form", { name: "Runs for Checks settled, not green" })).getByRole("button", { name: "Save for this entry point" }));
    await new Promise((r) => setTimeout(r, 50));
    expect(writes()).toHaveLength(0);
    expect((api.rules[0] as Rule & { max_attempts?: number }).max_attempts).toBe(9);
  });
});
