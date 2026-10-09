import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAgentState } from "../../agent-state/store";
import type { Condition, Rule } from "../../api/types";
import { SELECTED_RULE_ID } from "../../fixtures/rules-fixture";
import { createFakeApi, fetchFor, handle, withActiveRuns, type FakeApi } from "../../rules/fake-api";
import Rules from "../../routes/Rules";
import { FOLD_RULES, FOLD_WORKFLOWS, ON_FAILURE, RUN_KEY } from "./fixture";
import NewRule from "./NewRule";
import SimpleView, { type SimpleViewProps } from "./SimpleView";

type User = ReturnType<typeof userEvent.setup>;
let api: FakeApi;

const NOW = Date.parse("2026-10-09T12:00:00Z");
const failure = { status: 503, code: "store_down", message: "store unreachable" };

/** The fold fixture in the fake API, listed once so every rule carries its `updated_at`. */
function foldApi(edit?: (rules: Rule[]) => void): FakeApi {
  const fake = createFakeApi(NOW);
  fake.rules = structuredClone(FOLD_RULES);
  edit?.(fake.rules);
  fake.workflows = structuredClone(FOLD_WORKFLOWS);
  handle(fake, "GET", "/rules", new URLSearchParams());
  fake.calls = [];
  return fake;
}

function use(fake: FakeApi) {
  api = fake;
  vi.stubGlobal("fetch", fetchFor(api));
}

function renderSimple(props: Partial<SimpleViewProps> = {}) {
  return render(
    <MemoryRouter>
      <SimpleView workflowId="pr-fix" {...props} />
    </MemoryRouter>,
  );
}

const writes = () => api.calls.filter((c) => c.method !== "GET");
const sent = (method: string, path: string) => api.calls.filter((c) => c.method === method && c.path === path);
const entry = (name: string) => screen.getByRole("group", { name: `Entry point: ${name}` });
const findEntry = (name: string) => screen.findByRole("group", { name: `Entry point: ${name}` });
const then = (name: string) => screen.getByRole("group", { name });

async function expand(user: User, name: string) {
  const group = await findEntry(name);
  const toggle = within(group).getByRole("button", { name: `Expand ${name}` });
  await user.click(toggle);
  return entry(name);
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
    renderSimple();
    const view = await screen.findByRole("region", { name: "Simple view" });
    expect(within(view).getByRole("heading", { level: 2, name: "When" })).toBeInTheDocument();
    const names = within(view)
      .getAllByRole("group", { name: /^Entry point: / })
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

  it("opens the first entry point: trigger, condition, placement and attempt counting", async () => {
    renderSimple();
    const first = await findEntry("Checks settled, not green");
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
    renderSimple({ entry: "pr-fixer-refix" });
    const refix = await findEntry("Review asked for changes");
    expect(within(refix).getByRole("button", { name: "Collapse Review asked for changes" })).toBeInTheDocument();
    expect(within(refix).getByLabelText("Continues from")).toHaveValue("review-commit");
    const rows = within(refix).getByRole("list", { name: "Only if all of" });
    expect(within(rows).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "outputs.review=\"request_changes\"",
    ]);
    // one entry open at a time
    await user.click(within(refix).getByRole("button", { name: "Collapse Review asked for changes" }));
    expect(within(entry("Review asked for changes")).getByRole("button", { name: "Expand Review asked for changes" }))
      .toHaveAttribute("aria-expanded", "false");
  });

  it("draws the workflow's steps between When and Then", async () => {
    renderSimple({ def: FOLD_WORKFLOWS[0] });
    const steps = await screen.findByRole("list", { name: "Steps" });
    expect(within(steps).getAllByRole("listitem").map((li) => li.getAttribute("aria-label"))).toEqual([
      "Start", "Quiet period", "Fix the PR", "Test gate", "End",
    ]);
  });
});

describe("Then: continues into, ends here, on failure, runs", () => {
  it("shows the chain onward as read-only links and the workflow's shared Then values", async () => {
    renderSimple();
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
    renderSimple({ workflowId: "publish-fix" });
    await findEntry("Any run failed");
    expect(entry("Any run failed")).toHaveTextContent("from any workflow");
    expect(within(entry("Any run failed")).queryByLabelText("Continues from")).not.toBeInTheDocument();
    expect(then("Continues into")).toHaveTextContent("Nothing continues from here.");
  });
});

describe("shared values once, differing ones per entry", () => {
  it("a value is shared only when every entry holds it identically; otherwise each entry shows its own", async () => {
    use(foldApi((rules) => {
      rules[1].placement = { machine: "thor" };
      rules[3].action = { ...rules[3].action, name: "Say the refix ended" };
    }));
    const user = userEvent.setup();
    renderSimple();
    await findEntry("Checks settled, not green");
    // three on spark2, one on thor: no majority wins, every entry carries its own placement
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    expect(shared).toHaveTextContent("evaluates: differs per entry point");
    expect(shared).not.toHaveTextContent("spark2");
    expect(shared).toHaveTextContent("4 overrides");
    for (const name of ["Trusted PR comment", "Trusted review", "Review asked for changes"]) {
      expect(entry(name)).toHaveTextContent("override");
    }
    expect(within(entry("Checks settled, not green")).getByRole("list", { name: "Overrides" })).toHaveTextContent("evaluates on spark2");
    const comment = await expand(user, "Trusted PR comment");
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

/** Run the same user flow on the Rules tab and in the Simple view; return each one's PUT bodies. */
async function putBodies(
  ruleId: string,
  workflowId: string,
  flow: (user: User, scope: HTMLElement) => Promise<void>,
  setup?: (fake: FakeApi) => void,
) {
  const bodies: unknown[][] = [];
  for (const where of ["rules", "simple"] as const) {
    const fake = createFakeApi(NOW);
    setup?.(fake);
    use(fake);
    const user = userEvent.setup();
    const view = where === "rules"
      ? render(
        <MemoryRouter initialEntries={[`/rules/${ruleId}`]}>
          <Routes><Route path="/rules/:ruleId?" element={<Rules />} /></Routes>
        </MemoryRouter>,
      )
      : render(<MemoryRouter><SimpleView workflowId={workflowId} entry={ruleId} /></MemoryRouter>);
    const name = fake.rules.find((r) => r.id === ruleId)!.name;
    const scope = where === "rules"
      ? await screen.findByRole("main")
      : await findEntry(name);
    if (where === "rules") await screen.findByRole("heading", { level: 1, name });
    await flow(user, scope);
    await waitFor(() => expect(fake.calls.some((c) => c.method !== "GET")).toBe(true));
    await waitFor(() => expect(screen.queryByRole("form")).not.toBeInTheDocument());
    bodies.push(fake.calls.filter((c) => c.method !== "GET").map((c) => [c.method, c.path, c.body]));
    view.unmount();
  }
  return bodies;
}

describe("the same rule document the Rules tab saved", () => {
  it("the edit form: name, trigger, action and placement", async () => {
    const [rules, simple] = await putBodies(SELECTED_RULE_ID, "build-image", async (user, scope) => {
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
    const [rules, simple] = await putBodies("train-batch", "review-pr", async (user, scope) => {
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
    const [rules, simple] = await putBodies(SELECTED_RULE_ID, "build-image", async (user, scope) => {
      await user.selectOptions(within(scope).getByLabelText("Add may run after"), "train-batch");
    });
    expect(rules).toHaveLength(1);
    expect(simple).toEqual(rules);
    expect((simple[0] as unknown[])[2]).toMatchObject({ may_after: ["train-batch"] });
  });

  it("removing a relationship", async () => {
    const [rules, simple] = await putBodies(SELECTED_RULE_ID, "build-image", async (user, scope) => {
      await user.click(within(scope).getByRole("button", { name: "Remove: must run after Review on approve" }));
    });
    expect(rules).toHaveLength(1);
    expect(simple).toEqual(rules);
  });

  it("enable / disable", async () => {
    const [rules, simple] = await putBodies(SELECTED_RULE_ID, "build-image", async (user) => {
      await user.click(screen.getAllByRole("switch", { name: "Build and publish enabled" })[0]);
    });
    expect(simple).toEqual(rules);
    expect(simple).toEqual([["POST", `/rules/${SELECTED_RULE_ID}/disable`, undefined]]);
  });
});

describe("one entry point, one rule", () => {
  it("renaming an entry point issues one PUT, for its rule only", async () => {
    const user = userEvent.setup();
    renderSimple();
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
    renderSimple();
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
    renderSimple();
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
    renderSimple({ workflowId: "publish-fix", entry: "any-failure" });
    const any = await findEntry("Any run failed");
    const sw = within(any).getByRole("switch", { name: "Counts toward the attempt budget" });
    expect(sw).toHaveAttribute("aria-disabled", "true");
    expect(any).toHaveTextContent("opting out needs a run key");
    await user.click(sw);
    expect(writes()).toHaveLength(0);
  });

  it("adds an entry point: a new rule that starts this workflow", async () => {
    const user = userEvent.setup();
    renderSimple();
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
    renderSimple();
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
    renderSimple();
    const comment = await expand(user, "Trusted PR comment");
    await user.click(within(comment).getByRole("button", { name: "History of Trusted PR comment" }));
    const history = await within(comment).findByRole("list", { name: "Last runs" });
    await waitFor(() => expect(history).toHaveTextContent("superseded by Checks settled, not green"));
  });

  it("describe reads GET /rules/{id}/describe", async () => {
    const user = userEvent.setup();
    renderSimple();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "About Checks settled, not green" }));
    const panel = within(first).getByRole("dialog", { name: "About Checks settled, not green" });
    await waitFor(() => expect(panel).toHaveTextContent("When github.pr.checks_settled"));
    expect(sent("GET", "/rules/pr-fixer-checks/describe")).toHaveLength(1);
  });

  it("disabling with runs going offers to stop them, through POST /rules/{id}/stop-runs", async () => {
    withActiveRuns(api, "pr-fixer-checks", 2);
    const user = userEvent.setup();
    renderSimple();
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("switch", { name: "Checks settled, not green enabled" }));
    expect(await screen.findByText(/Stop 2 current runs\?/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Approve/ }));
    expect(await screen.findByText("Stopped 2 runs of Checks settled, not green.")).toBeInTheDocument();
    expect(sent("POST", "/rules/pr-fixer-checks/stop-runs")).toEqual([
      { method: "POST", path: "/rules/pr-fixer-checks/stop-runs", body: { apply: true } },
    ]);
  });
});

async function editRunKey(user: User, key: string) {
  await findEntry("Checks settled, not green");
  await user.click(within(then("Runs")).getByRole("button", { name: "Edit runs" }));
  const form = screen.getByRole("form", { name: "Runs" });
  await user.clear(within(form).getByLabelText("Run key"));
  await user.type(within(form).getByLabelText("Run key"), key.replaceAll("{", "{{"));
  await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
}

describe("a shared edit fans out, rule by rule", () => {
  it("shows a partial failure per rule with its old value and a retry; the others saved", async () => {
    api.failNext["PUT /rules/pr-fixer-comment"] = failure;
    const user = userEvent.setup();
    renderSimple();
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
    renderSimple();
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
    renderSimple();
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
    renderSimple();
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
    renderSimple();
    await findEntry("Checks settled, not green");
    const shared = screen.getByRole("group", { name: "Shared by every entry point" });
    await user.click(within(shared).getByRole("button", { name: /evaluates on spark2/ }));
    await user.selectOptions(screen.getByLabelText("Evaluates on"), "thor");
    await user.click(screen.getByRole("button", { name: "Save for every entry point" }));
    await waitFor(() => expect(writes().filter((c) => c.method === "PUT")).toHaveLength(4));
    for (const put of writes()) expect((put.body as Rule).placement).toEqual({ machine: "thor" });
  });
});

describe("a continuation's predecessor", () => {
  it("rewrites exactly the data.workflow_id compare, and never offers its own workflow or none", async () => {
    const user = userEvent.setup();
    renderSimple({ entry: "pr-fixer-refix" });
    const refix = await findEntry("Review asked for changes");
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

describe("D7: a rule without a workflow", () => {
  it("offers to create its workflow, then points the rule at it", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    renderSimple({ entry: "lone-alert", onCreated });
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    expect(within(offer).getByLabelText("Workflow id")).toHaveValue("lone-alert");
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("lone-alert"));
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["POST /workflows", "PUT /rules/lone-alert"]);
    expect(writes()[0].body).toEqual({ id: "lone-alert", name: "Lone alert", steps: [], edges: [] });
    expect(writes()[1].body).toEqual({ ...FOLD_RULES[7], workflow: { id: "lone-alert" } });
    expect(await within(offer).findByText(/Lone alert now starts/)).toBeInTheDocument();
  });

  it("a failed attach deletes the new workflow again and says so, with a retry", async () => {
    api.failNext["PUT /rules/lone-alert"] = { status: 422, code: "invalid_rule", message: "refused" };
    const user = userEvent.setup();
    renderSimple({ entry: "lone-alert" });
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    expect(await within(offer).findByText(/was deleted again/)).toBeInTheDocument();
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /workflows", "PUT /rules/lone-alert", "DELETE /workflows/lone-alert",
    ]);
    expect(within(offer).getByText(/refused/)).toBeInTheDocument();
    expect(within(offer).getByRole("button", { name: "Create its workflow" })).toBeEnabled();
  });

  it("an orphan wrapper is flagged with a fix action", async () => {
    api.failNext["PUT /rules/lone-alert"] = { status: 422, code: "invalid_rule", message: "refused" };
    api.failNext["DELETE /workflows/lone-alert"] = failure;
    const user = userEvent.setup();
    renderSimple({ entry: "lone-alert" });
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    expect(await within(offer).findByRole("alert")).toHaveTextContent(/lone-alert was created but Lone alert does not use it/);
    await user.click(within(offer).getByRole("button", { name: "Attach Lone alert to lone-alert" }));
    await waitFor(() => expect(sent("PUT", "/rules/lone-alert")).toHaveLength(2));
    expect(await within(offer).findByText(/Lone alert now starts/)).toBeInTheDocument();
  });
});

describe("deleting entry points", () => {
  it("deleting the last entry point of a stepless workflow offers to delete the workflow too", async () => {
    const user = userEvent.setup();
    renderSimple({ workflowId: "review-commit", def: FOLD_WORKFLOWS[1] });
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
    renderSimple({ def: FOLD_WORKFLOWS[0] });
    const first = await findEntry("Checks settled, not green");
    await user.click(within(first).getByRole("button", { name: "Delete Checks settled, not green" }));
    expect(await screen.findByText("Deleted Checks settled, not green")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Delete the workflow/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Undo" }));
    expect(await findEntry("Checks settled, not green")).toBeInTheDocument();
    expect(sent("POST", "/rules/pr-fixer-checks/restore")).toHaveLength(1);
  });
});

const KEY_OF = (id: string) => (api.rules.find((r) => r.id === id) as Rule & { concurrency_key?: string }).concurrency_key;

describe("review follow-up: shared values, budgets and run edits", () => {
  it("after a partial failure, the failed rule is the override with its old value, even when it came first", async () => {
    api.failNext["PUT /rules/pr-fixer-checks"] = failure;
    const user = userEvent.setup();
    renderSimple();
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
    renderSimple();
    await editRunKey(user, "pr:{trigger.data.number}");
    await waitFor(() => expect(writes().filter((c) => c.method === "PUT")).toHaveLength(4));
    const comment = sent("PUT", "/rules/pr-fixer-comment")[0].body as Rule & { max_attempts?: number };
    expect(comment.max_attempts).toBe(5);
    expect(KEY_OF("pr-fixer-comment")).toBe("pr:{trigger.data.number}");
  });

  it("refuses a budget on a rule outside the attempt budget, and says why", async () => {
    const user = userEvent.setup();
    renderSimple({ workflowId: "review-commit" });
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
    renderSimple();
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

describe("review follow-up: conditions", () => {
  it("an entry's existing condition terms can be removed and added to, one PUT each", async () => {
    const user = userEvent.setup();
    renderSimple();
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
    renderSimple({ entry: "pr-fixer-refix" });
    const refix = await findEntry("Review asked for changes");
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
    renderSimple({ workflowId: "publish-fix" });
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
  it("to the opener after cancel or save, and to the restored entry after undo", async () => {
    const user = userEvent.setup();
    renderSimple();
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

    await user.click(within(entry("Checks settled, not green")).getByRole("button", { name: "Delete Checks settled, not green" }));
    const undo = await screen.findByRole("button", { name: "Undo" });
    await waitFor(() => expect(undo).toHaveFocus());
    await user.click(undo);
    await waitFor(() => expect(within(entry("Checks settled, not green")).getByRole("button", { name: "Collapse Checks settled, not green" })).toHaveFocus());
  });
});

describe("review follow-up: D7 when the attach may have committed", () => {
  /** The PUT commits but its answer is lost (503), and the reconciliation re-read (the third GET: check, check-before-write, reconcile) fails. */
  function lostAttach() {
    const base = fetchFor(api);
    let reads = 0;
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "PUT" && url.endsWith("/rules/lone-alert")) {
        await base(input, init);
        return new Response(JSON.stringify({ error: { code: "bad_gateway", message: "lost", errors: [] } }), { status: 503 });
      }
      if (method === "GET" && url.endsWith("/rules/lone-alert") && ++reads === 3) {
        return new Response(JSON.stringify({ error: { code: "store_down", message: "down", errors: [] } }), { status: 503 });
      }
      return base(input, init);
    }) as typeof fetch);
  }

  it("re-reads the rule before calling it an orphan, and shows it attached when it is", async () => {
    lostAttach();
    const onCreated = vi.fn();
    const user = userEvent.setup();
    renderSimple({ entry: "lone-alert", onCreated });
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    expect(await within(offer).findByText(/Lone alert now starts/)).toBeInTheDocument();
    expect(onCreated).toHaveBeenCalledWith("lone-alert");
    expect(sent("DELETE", "/workflows/lone-alert")).toHaveLength(0);
  });

  it("Attach re-reads the rule and writes from what is stored now", async () => {
    api.failNext["PUT /rules/lone-alert"] = { status: 422, code: "invalid_rule", message: "refused" };
    api.failNext["DELETE /workflows/lone-alert"] = failure;
    const user = userEvent.setup();
    renderSimple({ entry: "lone-alert" });
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    await within(offer).findByRole("alert");
    // someone renames the rule meanwhile: the attach starts from that version, not the old snapshot
    Object.assign(api.rules.find((r) => r.id === "lone-alert")!, { name: "Lone alert (renamed)", updated_at: "2026-10-09T13:00:00Z" });
    await user.click(within(offer).getByRole("button", { name: "Attach Lone alert to lone-alert" }));
    expect(await within(offer).findByText(/now starts/)).toBeInTheDocument();
    const last = sent("PUT", "/rules/lone-alert").at(-1)!.body as Rule;
    expect(last).toMatchObject({ name: "Lone alert (renamed)", workflow: { id: "lone-alert" } });
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
    use(createFakeApi(NOW));
    let user = userEvent.setup();
    const tab = render(
      <MemoryRouter initialEntries={["/rules/train-batch"]}>
        <Routes><Route path="/rules/:ruleId?" element={<Rules />} /></Routes>
      </MemoryRouter>,
    );
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    await fill(user);
    await waitFor(() => expect(sent("POST", "/rules")).toHaveLength(1));
    const fromTab = sent("POST", "/rules")[0].body as Rule;
    tab.unmount();

    use(createFakeApi(NOW));
    user = userEvent.setup();
    render(<MemoryRouter><SimpleView workflowId="build-image" /></MemoryRouter>);
    await findEntry("Build and publish");
    await user.click(screen.getByRole("button", { name: "Entry point" }));
    await fill(user);
    await waitFor(() => expect(sent("POST", "/rules")).toHaveLength(1));
    expect(sent("POST", "/rules")[0].body).toEqual({ ...fromTab, workflow: { id: "build-image", inputs: {} } });
  });

  it("New rule creates the rule, then gives it a stepless workflow at once (D7) and reports its id", async () => {
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCreated={onCreated} onCancel={vi.fn()} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await waitFor(() => expect(sent("GET", "/workflows")).toHaveLength(1));
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("push-seen"));
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["POST /rules", "POST /workflows", "PUT /rules/push-seen"]);
    expect(writes()[1].body).toEqual({ id: "push-seen", name: "Push seen", steps: [], edges: [] });
    expect(api.rules.find((r) => r.id === "push-seen")?.workflow).toEqual({ id: "push-seen" });
    expect(await screen.findByText(/Push seen now starts Push seen/)).toBeInTheDocument();
  });

  it("New rule keeps the rule and offers the fix when the automatic D7 leaves an orphan", async () => {
    api.failNext["PUT /rules/push-seen"] = { status: 422, code: "invalid_rule", message: "refused" };
    api.failNext["DELETE /workflows/push-seen"] = failure;
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCreated={onCreated} onCancel={vi.fn()} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("push-seen was created but Push seen does not use it");
    expect(onCreated).not.toHaveBeenCalled();
    expect(api.rules.some((r) => r.id === "push-seen")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Attach Push seen to push-seen" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("push-seen"));
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
    renderSimple({ workflowId: "publish-fix" });
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
    renderSimple({ workflowId: "publish-fix" });
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

  it("New rule runs create-then-D7 once: no second submit, no cancel while it is in flight", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const base = fetchFor(api);
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? "GET").toUpperCase() === "POST" && String(input).endsWith("/rules")) await gate;
      return base(input, init);
    }) as typeof fetch);
    const onCreated = vi.fn();
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCreated={onCreated} onCancel={onCancel} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await waitFor(() => expect(sent("GET", "/workflows")).toHaveLength(1));
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    const create = within(form).getByRole("button", { name: "Create rule" });
    await user.click(create);
    await waitFor(() => expect(create).toBeDisabled());
    expect(within(form).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(within(form).getByLabelText("Name")).toBeDisabled();
    await user.click(create);
    await user.keyboard("{Escape}");
    expect(onCancel).not.toHaveBeenCalled();
    release();
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("push-seen"));
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["POST /rules", "POST /workflows", "PUT /rules/push-seen"]);
  });

  it("differing run keys and budgets can be cleared for every entry point; untouched mixed fields send nothing", async () => {
    use(foldApi((rules) => {
      (rules[1] as Rule & { max_attempts?: number }).max_attempts = 5;
      (rules[2] as Rule & { concurrency_key?: string }).concurrency_key = "other:{trigger.data.number}";
    }));
    const user = userEvent.setup();
    renderSimple();
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

describe("full-pass review follow-up", () => {
  it("from the mixed state, Placement, Ends here and On failure write nothing until a value is picked", async () => {
    use(foldApi((rules) => {
      rules[1].placement = { machine: "thor" };
      rules[3].action = { ...rules[3].action, name: "Other end" };
      rules[2].on_failure = null;
    }));
    const user = userEvent.setup();
    renderSimple();
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
    renderSimple({ workflowId: "publish-fix" });
    await findEntry("Publish when approved");
    await user.click(within(then("On failure")).getByRole("button", { name: "Edit on failure" }));
    await user.click(within(screen.getByRole("form", { name: "On failure" })).getByRole("button", { name: "Save for every entry point" }));
    expect(writes()).toHaveLength(0);
  });

  it("refuses a run key the server would refuse, and junk in the budget", async () => {
    const user = userEvent.setup();
    renderSimple();
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
    renderSimple({ workflowId: "publish-fix" });
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
    renderSimple();
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
    renderSimple();
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
    const view = render(<MemoryRouter><SimpleView workflowId="pr-fix" /></MemoryRouter>);
    await editRunKey(user, "pr:{trigger.data.number}");
    await screen.findByRole("region", { name: "Save results" });
    view.rerender(<MemoryRouter><SimpleView workflowId="publish-fix" /></MemoryRouter>);
    const first = await findEntry("Publish when approved");
    expect(within(first).getByRole("button", { name: "Collapse Publish when approved" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Save results" })).not.toBeInTheDocument();
  });

  it("New rule moves focus to the error when the create is refused", async () => {
    api.failNext["POST /rules"] = { status: 422, code: "invalid_rule", message: "refused" };
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCancel={vi.fn()} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("refused");
    await waitFor(() => expect(alert).toHaveFocus());
  });
});
