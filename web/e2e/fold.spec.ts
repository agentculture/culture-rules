import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import type { Rule } from "../src/api/types";
import { foldModel } from "../src/fold/model";
import { mockDocumentChainApi, DOCUMENT_CHAIN_RULES, DOCUMENT_CHAIN_WORKFLOWS, writesOf } from "./fixtures/document-chain";
import { withView } from "./fixtures/view";

/** Exercise chain navigation and rule editing through a stateful API. */
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

const COMMENT = "Trusted PR comment";
const REVIEW = "Trusted review";

/** The shared run key, edited once for every entry point of prepare-document (a fan-out). */
async function editSharedRunKey(page: Page, key: string) {
  const runs = page.getByRole("group", { name: "Runs", exact: true });
  await runs.getByRole("button", { name: "Edit runs" }).click();
  const form = page.getByRole("form", { name: "Runs" });
  await form.getByLabel("Run key").fill(key);
  await form.getByRole("button", { name: "Save for every entry point" }).click();
}

test.describe("the fold against the fake API (Document chain)", () => {
  test("the list folds the stored Document chain: chains, workflows and entry points computed from its rules", async ({ page }) => {
    await withView(page); // entry points are edited in Detailed
    await mockDocumentChainApi(page);
    const model = foldModel(DOCUMENT_CHAIN_RULES, DOCUMENT_CHAIN_WORKFLOWS);
    expect([model.chains.length, model.workflows.length, model.entryPoints.length, model.continuations.length])
      .toEqual([1, 3, 3, 4]);
    await page.goto("/workflows");
    await untilReady(page);
    // d13: the overview is the cards only. The chain view is reached from an open workflow's head.
    await expect(page.getByRole("navigation", { name: "Chains and housekeeping" })).toHaveCount(0);
    await expect(page.getByRole("region", { name: "Workflows overview" })).toContainText(`${model.workflows.length} workflows.`);
    const n = (count: number, noun: string) => `${count} ${noun}${count === 1 ? "" : "s"}`;
    for (const chain of model.chains.filter((c) => c.workflowIds.length > 1)) {
      await page.goto(`/workflows?id=${chain.workflowIds[0]}`);
      await untilReady(page);
      await page.getByRole("link", { name: "See it as one chain" }).click();
      const view = page.getByRole("region", { name: "Chain view" });
      await expect(view).toContainText(
        `${n(chain.entryPoints.length + chain.continuations.length, "rule")} → ${n(chain.entryPoints.length, "entry point")}, ${n(chain.continuations.length, "continuation")}, ${n(chain.workflowIds.length, "workflow")}`,
      );
    }
    expect(model.chains.reduce((n, c) => n + c.entryPoints.length + c.continuations.length, 0)).toBe(DOCUMENT_CHAIN_RULES.length - model.d7Candidates.length);
    const state = (await agentState(page)).workflows;
    expect(state.chains).toBe(1);
    expect(state.without_workflow).toEqual(["lone-alert"]);
    // The synthetic chain includes a disabled rule: the disabled tags must still read (AA contrast).
    const bad = (await new AxeBuilder({ page }).analyze()).violations
      .filter((v) => v.impact === "serious" || v.impact === "critical")
      .map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(" ")}`);
    expect(bad).toEqual([]);
  });

  test("editing one entry point's condition issues exactly 1 rule PUT, for its rule", async ({ page }) => {
    await withView(page); // entry points are edited in Detailed
    const api = await mockDocumentChainApi(page);
    const quiet = trackNetwork(page);
    const card = await openEntry(page, "prepare-document", "document-chain-comment", COMMENT);
    await card.getByRole("button", { name: 'Remove condition body = "/fix"', exact: true }).click();
    await expect.poll(() => writesOf(api)).toEqual(["PUT /rules/document-chain-comment"]);
    const put = api.calls.find((c) => c.method === "PUT")!;
    expect((put.body as Rule).condition).toBeNull();
    // Settled: the saved rule is drawn (its row gone), then the network goes quiet. Only then is
    // "exactly 1" checked, so a late second PUT cannot slip past.
    await expect(card.getByRole("button", { name: /^Remove condition body/ })).toHaveCount(0);
    await quiet();
    // The other entry points kept their own conditions: nothing else was written.
    expect(writesOf(api)).toEqual(["PUT /rules/document-chain-comment"]);
  });

  test("a fan-out with one failing PUT shows that rule as an override with its old value and a retry; the others saved", async ({ page }) => {
    await withView(page); // entry points are edited in Detailed
    const api = await mockDocumentChainApi(page);
    const old = stored(DOCUMENT_CHAIN_RULES.find((r) => r.id === "document-chain-comment")!).concurrency_key;
    api.failNext["PUT /rules/document-chain-comment"] = { status: 503, code: "store_down", message: "store unreachable" };
    await openEntry(page, "prepare-document", "document-chain-checks", "Checks settled, not green");
    await editSharedRunKey(page, "pr:{trigger.data.number}");

    const results = page.getByRole("region", { name: "Save results" });
    const row = (name: string) => results.getByRole("listitem").filter({ has: page.getByText(name, { exact: true }) });
    await expect(results.getByRole("listitem")).toHaveCount(4);
    await expect(results.locator('[data-status="saved"]')).toHaveCount(3);
    await expect(row(COMMENT)).toHaveAttribute("data-status", "failed");
    await expect(row(COMMENT)).toContainText("store unreachable");
    await expect(row(COMMENT)).toContainText(`keeps ${old}`);
    // The three others are stored with the new key; the failed one kept its own.
    const keys = () =>
      Object.fromEntries(api.rules.filter((r) => r.workflow?.id === "prepare-document").map((r) => [r.id, stored(r).concurrency_key]));
    expect(keys()).toEqual({
      "document-chain-checks": "pr:{trigger.data.number}",
      "document-chain-comment": old,
      "document-chain-refix": "pr:{trigger.data.number}",
      "document-chain-review": "pr:{trigger.data.number}",
    });
    // The failed rule is the override, with its old value.
    const overrides = page.getByRole("group", { name: "Runs", exact: true }).getByRole("list", { name: "Overrides" });
    await expect(overrides).toContainText(`${COMMENT}: ${old}`);
    await expect(entry(page, COMMENT)).toContainText("override");

    await results.getByRole("button", { name: `Retry ${COMMENT}` }).click();
    await expect(row(COMMENT)).toHaveAttribute("data-status", "saved");
    await expect(overrides).toHaveCount(0);
    expect(api.calls.filter((c) => c.method === "PUT" && c.path === "/rules/document-chain-comment")).toHaveLength(2);
  });

  test("a concurrent change is re-read, skipped and flagged, never overwritten; the others save", async ({ page }) => {
    await withView(page); // entry points are edited in Detailed
    const api = await mockDocumentChainApi(page);
    await openEntry(page, "prepare-document", "document-chain-checks", "Checks settled, not green");
    // Someone else edits the review entry point after the page read it.
    const review = api.rules.find((r) => r.id === "document-chain-review")!;
    Object.assign(review, { max_attempts: 5, updated_at: "2026-10-09T12:30:00Z" });
    const before = stored(review).concurrency_key;
    await editSharedRunKey(page, "pr:{trigger.data.number}");

    const results = page.getByRole("region", { name: "Save results" });
    await expect(results.getByRole("listitem")).toHaveCount(4);
    const row = results.getByRole("listitem").filter({ has: page.getByText(REVIEW, { exact: true }) });
    await expect(row).toHaveAttribute("data-status", "skipped-changed");
    await expect(row).toContainText("changed since you opened it");
    await expect(row.getByRole("button", { name: `Apply to ${REVIEW} as it is now` })).toBeVisible();
    await expect(results.locator('[data-status="saved"]')).toHaveCount(3);
    expect(api.calls.filter((c) => c.method === "GET" && c.path === "/rules/document-chain-review")).toHaveLength(1);
    expect(api.calls.filter((c) => c.method === "PUT" && c.path === "/rules/document-chain-review")).toHaveLength(0);
    expect(stored(review).concurrency_key).toBe(before);
    expect(stored(review).max_attempts).toBe(5);
  });

  test("old /rules links redirect: a known id to its entry point, an unknown id to a notice, /rules to the list", async ({ page }) => {
    await withView(page); // entry points are edited in Detailed
    await mockDocumentChainApi(page);
    await page.goto("/rules/document-chain-validate-document");
    await expect(page).toHaveURL(/\/workflows\?id=validate-document&entry=document-chain-validate-document$/);
    await untilReady(page);
    await expect(entry(page, "Validate the document").getByRole("button", { name: "Collapse Validate the document" }))
      .toBeVisible();
    expect((await agentState(page)).workflows).toMatchObject({ selected: "validate-document", entry: "document-chain-validate-document" });

    await page.goto("/rules/no-such-rule");
    await expect(page).toHaveURL(/\/workflows\?notice=rule-not-found&rule=no-such-rule$/);
    await expect(page.getByText("No rule “no-such-rule” any more")).toBeVisible();

    await page.goto("/rules");
    await expect(page).toHaveURL(/\/workflows$/);
    await untilReady(page);

    // The redirect replaces the history entry: Back never lands on a dead /rules page.
    await page.goto("/workflows?id=publish-document");
    await untilReady(page);
    await page.goto("/rules/document-chain-checks");
    await expect(page).toHaveURL(/\/workflows\?id=prepare-document&entry=document-chain-checks$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/workflows\?id=publish-document$/);
  });
});
