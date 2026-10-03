import { expect, test, type Page } from "@playwright/test";
import { mockApi } from "./fixtures/api";
import { mockActorsApi } from "./fixtures/actors";
import { mockStatistics } from "./fixtures/statistics";
import { mockWorkflowsApi } from "./fixtures/workflows";
import { REVIEW_PR } from "../src/workflows/fixture";

/**
 * Deviation d4: every interactive control has a hit area of at least 44x44
 * CSS px, while its *visual* size stays as the design canvas draws it (40px
 * pills, 46x28 switches, 40px icon buttons). The board-measurement tests in
 * the other specs pin the visual sizes; this one pins the hit area.
 *
 * For every visible button, link, switch, tab, radio and select, the points
 * 21px left/right/above/below its centre (inside a centred 44x44 square)
 * must hit-test (`document.elementFromPoint`) to the control itself or to
 * something inside it.
 */
const CONTROLS = 'button, a[href], [role="switch"], [role="tab"], [role="radio"], select';
const REACH = 21;

async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

/** Controls whose 44x44 square misses them; `within` narrows the sweep to one region. */
async function smallHitAreas(page: Page, within?: string): Promise<string[]> {
  const controls = within ? page.locator(within).locator(CONTROLS) : page.locator(CONTROLS);
  const count = await controls.count();
  const misses: string[] = [];
  for (let i = 0; i < count; i += 1) {
    const control = controls.nth(i);
    if (!(await control.isVisible())) continue;
    // Centred, so the whole 44x44 square is on screen (a control at the viewport's or a
    // scroller's edge — the canvas scrolls sideways beside the workflow list — would not be).
    await control.evaluate((el) => el.scrollIntoView({ block: "center", inline: "center" }));
    const miss = await control.evaluate((el, reach) => {
      const box = el.getBoundingClientRect();
      if (box.width < 2 || box.height < 2) return null; // visually hidden (sr-only)
      // off-screen until focused (the skip link): measured when it is shown
      if (box.bottom <= 0 || box.right <= 0 || box.top >= innerHeight || box.left >= innerWidth)
        return null;
      const style = getComputedStyle(el);
      if (style.visibility === "hidden" || el.closest("[aria-hidden='true']")) return null;
      const cx = box.left + box.width / 2;
      const cy = box.top + box.height / 2;
      const name =
        el.getAttribute("aria-label") ||
        (el.textContent ?? "").trim().slice(0, 30) ||
        el.getAttribute("title") ||
        el.tagName.toLowerCase();
      const points: [number, number][] = [
        [cx - reach, cy],
        [cx + reach, cy],
        [cx, cy - reach],
        [cx, cy + reach],
      ];
      const failed = points.filter(([x, y]) => {
        const hit = document.elementFromPoint(x, y);
        return !(hit && (hit === el || el.contains(hit)));
      });
      return failed.length > 0
        ? `${el.tagName.toLowerCase()} "${name}" ${Math.round(box.width)}x${Math.round(box.height)}`
        : null;
    }, REACH);
    if (miss) misses.push(miss);
  }
  return misses;
}

const TABS: { name: string; path: string; mock: (page: Page) => Promise<unknown> }[] = [
  { name: "Rules", path: "/rules/build-and-publish", mock: (page) => mockApi(page) },
  { name: "Workflows", path: "/workflows?id=review-pr", mock: (page) => mockWorkflowsApi(page) },
  {
    name: "Workflows (empty state)",
    path: "/workflows",
    mock: async (page) => {
      await mockApi(page);
      await mockWorkflowsApi(page, []);
    },
  },
  { name: "Actors", path: "/actors", mock: (page) => mockActorsApi(page) },
  {
    name: "Statistics",
    path: "/statistics",
    mock: async (page) => {
      await mockApi(page);
      await mockStatistics(page);
    },
  },
];

test.describe("d4: every control has a 44x44 hit area", () => {
  for (const tab of TABS) {
    test(`${tab.name} tab and the header`, async ({ page }) => {
      await tab.mock(page);
      await page.goto(tab.path);
      await expect.poll(async () => (await agentState(page)).status).toBe("ready");
      expect(await smallHitAreas(page)).toEqual([]);
    });
  }

  test("Workflows tab: the New workflow name form", async ({ page }) => {
    await mockApi(page);
    await mockWorkflowsApi(page, []);
    await page.goto("/workflows");
    await expect.poll(async () => (await agentState(page)).status).toBe("ready");
    await page.getByRole("region", { name: "No workflows yet" }).getByRole("button", { name: "New workflow" }).click();
    await expect(page.getByRole("form", { name: "New workflow" })).toBeVisible();
    expect(await smallHitAreas(page)).toEqual([]);
  });

  for (const [label, start] of [
    ["one workflow", [REVIEW_PR]],
    ["no workflows", []],
  ] as const) {
    test(`Workflows tab: the list's rows and New button (${label})`, async ({ page }) => {
      await mockApi(page);
      await mockWorkflowsApi(page, [...start]);
      await page.goto("/workflows");
      await expect.poll(async () => (await agentState(page)).status).toBe("ready");
      const list = page.getByRole("navigation", { name: "Workflows" });
      await expect(list.getByRole("button", { name: "New workflow" })).toBeVisible();
      await expect(list.getByRole("link")).toHaveCount(start.length);
      expect(await smallHitAreas(page, 'nav[aria-label="Workflows"]')).toEqual([]);
      // The row itself is a large target too: at least 44px tall, as the New button is.
      for (const el of [list.getByRole("button", { name: "New workflow" }), ...(start.length ? [list.locator(".rule-row")] : [])]) {
        expect((await el.boundingBox())!.height).toBeGreaterThanOrEqual(44);
      }
    });
  }
});
