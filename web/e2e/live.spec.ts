import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { createFakeApi } from "../src/rules/fake-api";
import { statStatuses } from "../src/statistics/fixture";
import { mockApi } from "./fixtures/api";
import { LiveFeed } from "./fixtures/live";
import { mockRulesApi } from "./fixtures/rules";
import { mockStatistics } from "./fixtures/statistics";

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}
async function untilReady(page: Page) {
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
}
/** Where an old /rules/build-and-publish link lands: its entry point on its workflow. */
const ENTRY = "/workflows?id=build-image&entry=build-and-publish";
const seriousAxe = async (page: Page) =>
  (await new AxeBuilder({ page }).analyze()).violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id} — ${v.help}: ${v.nodes.map((n) => `${n.target} ${n.failureSummary}`).join(" | ")}`);

test.describe("live editor updates (h61 / c80)", () => {
  test("an entry point toggled in one browser shows in another without a reload, over one stream", async ({ browser }) => {
    const api = createFakeApi();
    const feed = new LiveFeed();
    const [a, b] = await Promise.all([browser.newContext(), browser.newContext()]);
    const pageA = await a.newPage();
    const pageB = await b.newPage();
    for (const page of [pageA, pageB]) {
      await mockRulesApi(page, api);
      await feed.broadcastWrites(page);
      await feed.attach(page);
    }
    // Rules live in their workflows now: Build and publish is build-image's entry point.
    await pageA.goto(ENTRY);
    await pageB.goto(ENTRY);
    await untilReady(pageA);
    await untilReady(pageB);

    const name = "Build and publish enabled";
    const swB = pageB.getByRole("group", { name: "Entry point: Build and publish" }).getByRole("switch", { name });
    await expect(swB).toHaveAttribute("aria-checked", "true");
    let navigations = 0;
    pageB.on("framenavigated", () => (navigations += 1));

    const swA = pageA.getByRole("group", { name: "Entry point: Build and publish" }).getByRole("switch", { name });
    await swA.click();
    await expect(swA).toHaveAttribute("aria-checked", "false");
    await expect(swB).toHaveAttribute("aria-checked", "false", { timeout: 10_000 });
    // The list's copy follows too: the same rule is listed under build-image, disabled.
    await expect(
      pageB.getByRole("navigation", { name: "Workflows" }).locator('.fold-entry[data-rule-id="build-and-publish"]'),
    ).toContainText("Disabled");
    expect(navigations).toBe(0);
    // One stream per page: the board's, which the Simple view shares (no second EventSource).
    const collections = feed.streams.map((u) => new URL(u).searchParams.get("collections"));
    expect(new Set(collections)).toEqual(new Set(["workflows,runs,rules,asks,rule_decisions"]));
    expect(await seriousAxe(pageB)).toEqual([]);
    await a.close();
    await b.close();
  });

  test("a skipped (superseded) entry point reads 'superseded by <rule>' in its last runs", async ({ page }) => {
    const api = createFakeApi();
    api.decisions = [
      {
        rule_id: "build-and-publish",
        event_id: "evt_7",
        reason: "superseded_by",
        by: ["review-on-approve"],
        message: "superseded by review-on-approve",
        at: new Date(api.now - 30 * 60_000).toISOString(),
        host: "spark",
      },
    ];
    await mockRulesApi(page, api);
    await page.goto(ENTRY);
    await untilReady(page);
    const card = page.getByRole("group", { name: "Entry point: Build and publish" });
    await card.getByRole("button", { name: "History of Build and publish" }).click();
    const runs = card.getByRole("list", { name: "Last runs" });
    const skip = runs.locator('[data-decision="superseded_by"]');
    await expect(skip).toContainText("superseded by Review on approve");
    await expect(skip).toContainText("skipped");
    // The runs read from the same GET /rules/{id}/history, newest first.
    await expect(runs.locator("[data-run-status]")).toHaveCount(4);
    expect(api.calls.some((c) => c.method === "GET" && c.path === "/rules/build-and-publish/history")).toBe(true);
    expect(await seriousAxe(page)).toEqual([]);
  });
});

test.describe("Statistics refreshes live (h33 / c49)", () => {
  test("the lane follows GET /machines/status between polls, and a stale host turns offline", async ({
    page,
  }) => {
    await page.clock.install({ time: Date.now() });
    await mockApi(page);
    await mockStatistics(page);
    const now = Date.now();
    let statuses = statStatuses(now);
    let reads = 0;
    await page.route("**/api/machines/status", async (route) => {
      reads += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ items: statuses }),
      });
    });
    await page.goto("/statistics");
    await untilReady(page);
    const thor = page.getByRole("region", { exact: true, name: "thor" });
    await expect(thor.getByText("+4 queued")).toBeVisible();

    // The API's next answer: thor's queue grew (a heartbeat, no store write).
    statuses = statuses.map((s) => (s.name === "thor" ? { ...s, queue_depth: 9 } : s));
    const before = reads;
    await page.clock.fastForward(10_500);
    await expect(thor.getByText("+9 queued")).toBeVisible();
    expect(reads).toBeGreaterThan(before);

    // spark2 stops beating: its last_seen stays put while the clock moves on.
    const spark2 = page.getByRole("region", { exact: true, name: "spark2" });
    await expect(spark2).toHaveAttribute("data-online", "true");
    await page.clock.fastForward(31_000);
    await expect(spark2).toHaveAttribute("data-online", "false");
    await expect(spark2.getByText("not reachable")).toBeVisible();
    expect(await seriousAxe(page)).toEqual([]);
  });
});
