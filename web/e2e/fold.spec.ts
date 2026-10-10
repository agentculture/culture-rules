import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { Rule } from "../src/api/types";
import { foldModel } from "../src/fold/model";
import { mockPrFixerApi, PR_FIXER_RULES, PR_FIXER_WORKFLOWS, writesOf } from "./fixtures/pr-fixer";

/**
 * The fold's own scenarios (spec c18, c19, h14; plan t9), against the
 * stateful fake API (src/rules/fake-api.ts) holding the stored PR fixer
 * (docs/rules/pr-fixer): the Workflows tab is where a rule is now edited, as
 * an entry point of the workflow it starts.
 */
async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function untilReady(page: Page) {
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
}

/**
 * Resolve once no request has been in flight for `quietMs` (a network-idle wait that works after
 * load, unlike waitForLoadState("networkidle")): a late extra write would be in flight or done.
 */
function trackNetwork(page: Page) {
  let inFlight = 0;
  let lastChange = Date.now();
  const bump = (n: number) => {
    inFlight += n;
    lastChange = Date.now();
  };
  page.on("request", () => bump(1));
  page.on("requestfinished", () => bump(-1));
  page.on("requestfailed", () => bump(-1));
  return (quietMs = 500) =>
    expect
      .poll(() => inFlight <= 0 && Date.now() - lastChange >= quietMs, { timeout: 10_000, intervals: [100] })
      .toBe(true);
}

const entry = (page: Page, name: string) => page.getByRole("group", { name: `Entry point: ${name}`, exact: true });

async function openEntry(page: Page, workflow: string, rule: string, name: string) {
  await page.goto(`/workflows?id=${workflow}&entry=${rule}`);
  await untilReady(page);
  const card = entry(page, name);
  await expect(card.getByRole("button", { name: `Collapse ${name}` })).toBeVisible();
  return card;
}

/** The run fields a stored rule carries beside the typed ones. */
type Stored = Rule & { concurrency_key?: string | null; max_attempts?: number | null };
const stored = (rule: Rule) => rule as Stored;

const COMMENT = "PR fixer: trusted PR comment";
const REVIEW = "PR fixer: trusted review";

/** The shared run key, edited once for every entry point of pr-fix (a fan-out). */
async function editSharedRunKey(page: Page, key: string) {
  const runs = page.getByRole("group", { name: "Runs", exact: true });
  await runs.getByRole("button", { name: "Edit runs" }).click();
  const form = page.getByRole("form", { name: "Runs" });
  await form.getByLabel("Run key").fill(key);
  await form.getByRole("button", { name: "Save for every entry point" }).click();
}

test.describe("the fold against the fake API (PR fixer)", () => {
  test("the list folds the stored PR fixer: chains, workflows and entry points computed from its rules", async ({ page }) => {
    await mockPrFixerApi(page);
    const model = foldModel(PR_FIXER_RULES, PR_FIXER_WORKFLOWS);
    // d2: 2 chains; since #35 6 workflows (queue-add, queue-progress); since d31 9 entry
    // points (pr-fixer-conflict); since d34 7 workflows (queue-stop), 11 entry points (the
    // two stop rules), 10 continuations; since d37 11 (pr-fixer-retry-failed); computed, not
    // assumed.
    expect([model.chains.length, model.workflows.length, model.entryPoints.length, model.continuations.length])
      .toEqual([2, 7, 11, 11]);
    await page.goto("/workflows");
    await untilReady(page);
    const list = page.getByRole("navigation", { name: "Workflows" });
    await expect(list.getByRole("article")).toHaveCount(model.chains.length);
    await expect(list).toContainText(`${model.workflows.length} workflows · ${model.entryPoints.length} entry points · was ${PR_FIXER_RULES.length} rules`);
    const state = (await agentState(page)).workflows;
    expect(state.chains).toBe(2);
    expect(state.without_workflow).toEqual([]);
    // Every stored PR fixer rule is disabled: the disabled badges must still read (AA contrast).
    const bad = (await new AxeBuilder({ page }).analyze()).violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(" ")}`);
    expect(bad).toEqual([]);
  });

  test("editing one entry point's condition issues exactly 1 rule PUT, for its rule", async ({ page }) => {
    const api = await mockPrFixerApi(page);
    const quiet = trackNetwork(page);
    const card = await openEntry(page, "queue-add", "pr-fixer-comment", COMMENT);
    await card.getByRole("button", { name: "Remove condition pr_enriched = true", exact: true }).click();
    await expect.poll(() => writesOf(api)).toEqual(["PUT /rules/pr-fixer-comment"]);
    const put = api.calls.find((c) => c.method === "PUT")!;
    const terms = ((put.body as Rule).condition as { args: unknown[] }).args;
    expect(JSON.stringify(terms)).not.toContain("data.pr_enriched");
    expect(terms).toHaveLength(9);
    // Settled: the saved rule is drawn (its row gone), then the network goes quiet. Only then is
    // "exactly 1" checked, so a late second PUT cannot slip past.
    await expect(card.getByRole("button", { name: /^Remove condition pr_enriched/ })).toHaveCount(0);
    await quiet();
    // The other entry points kept their own conditions: nothing else was written.
    expect(writesOf(api)).toEqual(["PUT /rules/pr-fixer-comment"]);
  });

  test("a fan-out with one failing PUT shows that rule as an override with its old value and a retry; the others saved", async ({ page }) => {
    const api = await mockPrFixerApi(page);
    const old = stored(PR_FIXER_RULES.find((r) => r.id === "pr-fixer-comment")!).concurrency_key;
    api.failNext["PUT /rules/pr-fixer-comment"] = { status: 503, code: "store_down", message: "store unreachable" };
    await openEntry(page, "queue-add", "pr-fixer-checks", "PR fixer: checks settled");
    await editSharedRunKey(page, "pr:{trigger.data.number}");

    const results = page.getByRole("region", { name: "Save results" });
    const row = (name: string) => results.getByRole("listitem").filter({ has: page.getByText(name, { exact: true }) });
    await expect(results.getByRole("listitem")).toHaveCount(8); // d37: + pr-fixer-retry-failed
    await expect(results.locator('[data-status="saved"]')).toHaveCount(7);
    await expect(row(COMMENT)).toHaveAttribute("data-status", "failed");
    await expect(row(COMMENT)).toContainText("store unreachable");
    await expect(row(COMMENT)).toContainText(`keeps ${old}`);
    // The seven others are stored with the new key; the failed one kept its own.
    const keys = () =>
      Object.fromEntries(api.rules.filter((r) => r.workflow?.id === "queue-add").map((r) => [r.id, stored(r).concurrency_key]));
    expect(keys()).toEqual({
      "pr-fixer-checks": "pr:{trigger.data.number}",
      "pr-fixer-comment": old,
      "pr-fixer-conflict": "pr:{trigger.data.number}",
      "pr-fixer-refix": "pr:{trigger.data.number}",
      "pr-fixer-retry": "pr:{trigger.data.number}",
      "pr-fixer-retry-failed": "pr:{trigger.data.number}",
      "pr-fixer-review": "pr:{trigger.data.number}",
      "pr-fixer-review-comment": "pr:{trigger.data.number}",
    });
    // The failed rule is the override, with its old value.
    const overrides = page.getByRole("group", { name: "Runs", exact: true }).getByRole("list", { name: "Overrides" });
    await expect(overrides).toContainText(`${COMMENT}: ${old}`);
    await expect(entry(page, COMMENT)).toContainText("override");

    await results.getByRole("button", { name: `Retry ${COMMENT}` }).click();
    await expect(row(COMMENT)).toHaveAttribute("data-status", "saved");
    await expect(overrides).toHaveCount(0);
    expect(api.calls.filter((c) => c.method === "PUT" && c.path === "/rules/pr-fixer-comment")).toHaveLength(2);
  });

  test("a concurrent change is re-read, skipped and flagged, never overwritten; the others save", async ({ page }) => {
    const api = await mockPrFixerApi(page);
    await openEntry(page, "queue-add", "pr-fixer-checks", "PR fixer: checks settled");
    // Someone else edits the review entry point after the page read it.
    const review = api.rules.find((r) => r.id === "pr-fixer-review")!;
    Object.assign(review, { max_attempts: 5, updated_at: "2026-10-09T12:30:00Z" });
    const before = stored(review).concurrency_key;
    await editSharedRunKey(page, "pr:{trigger.data.number}");

    const results = page.getByRole("region", { name: "Save results" });
    await expect(results.getByRole("listitem")).toHaveCount(8); // d37: + pr-fixer-retry-failed
    const row = results.getByRole("listitem").filter({ has: page.getByText(REVIEW, { exact: true }) });
    await expect(row).toHaveAttribute("data-status", "skipped-changed");
    await expect(row).toContainText("changed since you opened it");
    await expect(row.getByRole("button", { name: `Apply to ${REVIEW} as it is now` })).toBeVisible();
    await expect(results.locator('[data-status="saved"]')).toHaveCount(7);
    expect(api.calls.filter((c) => c.method === "GET" && c.path === "/rules/pr-fixer-review")).toHaveLength(1);
    expect(api.calls.filter((c) => c.method === "PUT" && c.path === "/rules/pr-fixer-review")).toHaveLength(0);
    expect(stored(review).concurrency_key).toBe(before);
    expect(stored(review).max_attempts).toBe(5);
  });

  test("old /rules links redirect: a known id to its entry point, an unknown id to a notice, /rules to the list", async ({ page }) => {
    await mockPrFixerApi(page);
    await page.goto("/rules/pr-fixer-review-commit");
    await expect(page).toHaveURL(/\/workflows\?id=review-commit&entry=pr-fixer-review-commit$/);
    await untilReady(page);
    await expect(entry(page, "PR fixer: review the fix").getByRole("button", { name: "Collapse PR fixer: review the fix" }))
      .toBeVisible();
    expect((await agentState(page)).workflows).toMatchObject({ selected: "review-commit", entry: "pr-fixer-review-commit" });

    await page.goto("/rules/no-such-rule");
    await expect(page).toHaveURL(/\/workflows\?notice=rule-not-found&rule=no-such-rule$/);
    await expect(page.getByText("No rule “no-such-rule” any more")).toBeVisible();

    await page.goto("/rules");
    await expect(page).toHaveURL(/\/workflows$/);
    await untilReady(page);

    // The redirect replaces the history entry: Back never lands on a dead /rules page.
    await page.goto("/workflows?id=publish-fix");
    await untilReady(page);
    await page.goto("/rules/pr-fixer-secrets");
    await expect(page).toHaveURL(/\/workflows\?id=report-secrets&entry=pr-fixer-secrets$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/workflows\?id=publish-fix$/);
  });
});
