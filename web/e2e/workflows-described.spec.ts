import { expect, test, type Locator, type Page } from "@playwright/test";
import { withView } from "./fixtures/view";
import { DESCRIBED, DESCRIBED_WORKFLOWS, mockDescribedApi, stepIds } from "./fixtures/described";

/**
 * Behavioural check of the workflow descriptions and the joined paragraph (plan t18; c21/h18,
 * c22/h19, c9): the committed ten workflows, served with the REAL API's describe paragraphs
 * (e2e/fixtures/describe-paragraphs.json, regenerated and compared by
 * tests/model/test_describe_paragraphs_fixture.py). Fails if a description, the paragraph, a
 * step, or the Notes is missing, or if the page shows a phrase the API did not send.
 */
async function agentState(page: Page) {
  return JSON.parse((await page.locator("#agent-state").textContent()) ?? "{}");
}

async function open(page: Page, path: string, view?: "detailed") {
  if (view) await withView(page, view);
  await mockDescribedApi(page);
  await page.goto(path);
  await expect.poll(async () => (await agentState(page)).status).toBe("ready");
}

const norm = (text: string) => text.replace(/\s+/g, " ").trim();

/** The text a reader sees: visually-hidden cues (.sv-sr) and the disabled tag (.sv-pill, d13) left out, whitespace normalised. */
async function visibleText(el: Locator): Promise<string> {
  return el.evaluate((node) => {
    const copy = node.cloneNode(true) as HTMLElement;
    copy.querySelectorAll(".sv-sr, .sv-pill").forEach((n) => n.remove());
    return Array.from(copy.querySelectorAll(".sv-line"), (l) => (l.textContent ?? "").replace(/\s+/g, " ").trim()).join("\n");
  });
}

const paragraph = (page: Page) => page.getByTestId("paragraph");
const WORKFLOWS = DESCRIBED_WORKFLOWS.map((w) => w.id);

test.describe("Workflow descriptions and the joined paragraph (t18)", () => {
  test("the fixture has the fourteen committed workflows, one with no description and a step with none", () => {
    expect(WORKFLOWS).toHaveLength(14);
    expect(DESCRIBED_WORKFLOWS.filter((w) => !w.description?.trim()).map((w) => w.id)).toEqual(["daily-newspaper"]);
    const gaps = Object.values(DESCRIBED).flatMap((d) => d.paragraph.segments.filter((s) => s.gap));
    expect(gaps.length).toBeGreaterThan(0);
  });

  test("/workflows: one card per workflow with its description (or the gap) and no pane", async ({ page }) => {
    await open(page, "/workflows");
    await expect(page.locator("aside.wf-pane")).toHaveCount(0);
    const cards = page.getByRole("list", { name: "All workflows" }).getByRole("listitem");
    await expect(cards).toHaveCount(14);
    for (const wf of DESCRIBED_WORKFLOWS) {
      const card = page.getByRole("link", { name: new RegExp(`^${wf.name}`) }).first();
      const expected = wf.description?.trim() || "No description yet";
      await expect(card.locator(".wf-tile__desc")).toHaveText(expected);
    }
  });

  test("pr-fix: Simple shows the description and the joined paragraph, Detailed the notes", async ({ page }) => {
    await open(page, "/workflows?id=pr-fix");
    const pr = DESCRIBED["pr-fix"];
    // The compact pane (a search box and names), the open workflow current.
    await expect(page.locator("aside.wf-pane").getByRole("searchbox", { name: "Search workflows" })).toBeVisible();
    await expect(page.locator("aside.wf-pane").getByRole("link", { name: DESCRIBED_WORKFLOWS.find((w) => w.id === "pr-fix")!.name, exact: true })).toHaveAttribute("aria-current", "page");
    // The workflow's own description, then How it runs.
    await expect(page.getByRole("region", { name: "Simple view" })).toBeVisible();
    await expect(page.getByLabel("Workflow description")).toHaveText(pr.description);
    await expect(page.getByRole("heading", { level: 2, name: "How it runs" })).toBeVisible();
    expect(await visibleText(paragraph(page))).toBe(pr.paragraph.text);
    // Simple is text only: no canvas, no entry-point editors.
    await expect(page.locator(".wf-canvas")).toHaveCount(0);
    await expect(page.getByRole("group", { name: /^Entry point:/ })).toHaveCount(0);

    // Detailed: the workflow's Notes at the top (d13), also in the inputs editor, and a step's Notes.
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.getByRole("group", { name: "Canvas view" }).getByRole("button", { name: "Detailed" }).click();
    const wf = DESCRIBED_WORKFLOWS.find((w) => w.id === "pr-fix")!;
    expect(wf.notes).toBeTruthy();
    await expect(page.getByRole("textbox", { name: "Notes for this workflow" })).toHaveValue(wf.notes!);
    await page.getByRole("group", { name: "Inputs", exact: true }).locator(".wf-card__io-title").click();
    await expect(page.getByRole("dialog", { name: "Edit inputs" }).getByRole("textbox", { name: "Workflow notes" })).toHaveValue(wf.notes!);
    await page.keyboard.press("Escape");
    const quiet = wf.steps!.find((s) => s.id === "quiet")!;
    expect(quiet.notes).toBeTruthy();
    await page.getByRole("group", { name: quiet.name ?? "quiet", exact: true }).locator(".wf-card__name").click();
    await page.getByRole("button", { name: `Edit ${quiet.name ?? "quiet"}`, exact: true }).click();
    const dialog = page.getByRole("dialog", { name: `Edit ${quiet.name ?? "quiet"}` });
    await expect(dialog.getByRole("textbox", { name: "Notes" })).toHaveValue(quiet.notes!);
    await expect(dialog.getByRole("textbox", { name: "One-line description" })).toHaveValue(quiet.description!);
  });

  for (const id of WORKFLOWS) {
    test(`${id}: Simple text equals the API paragraph; every step in order; no invented phrase`, async ({ page }) => {
      await open(page, `/workflows?id=${id}`);
      const { paragraph: p, description } = DESCRIBED[id];
      const wf = DESCRIBED_WORKFLOWS.find((w) => w.id === id)!;
      await expect(paragraph(page)).toBeVisible();

      // The editor's text equals the API describe paragraph.
      expect(await visibleText(paragraph(page))).toBe(p.text);
      // The workflow description, or the dashed gap when it has none.
      const lede = page.getByLabel("Workflow description");
      await expect(lede).toHaveText(description || "No description yet");
      if (!description) await expect(lede.locator(".sv-gap")).toHaveCount(1);

      // 100% of the steps appear, in the order the API sent them (a loop's body after the loop).
      const sent = p.segments.filter((s) => s.part === "step").map((s) => s.step);
      expect(sent).toEqual(stepIds(wf.steps));
      expect(await paragraph(page).locator("[data-step]").evaluateAll((els) => els.map((e) => e.getAttribute("data-step")))).toEqual(sent);

      // 0 invented phrases: every highlighted phrase is one of the segments' own words.
      const allowed = new Set(p.segments.flatMap((s) => [s.phrase, s.text].filter(Boolean).map((t) => norm(String(t)))));
      const shown = (await paragraph(page).locator(".sv-phrase").evaluateAll((els) => els.map((e) => e.textContent ?? ""))).map(norm);
      expect(shown.length).toBeGreaterThan(0);
      for (const phrase of shown) {
        expect(allowed.has(phrase) || [...allowed].some((a) => a.includes(phrase)), `invented phrase: ${phrase}`).toBe(true);
      }

      // c9: no committed workflow reads a fallback line.
      await expect(paragraph(page).locator(".sv-fallback")).toHaveCount(0);
      expect(p.segments.filter((s) => s.fallback)).toEqual([]);
    });
  }

  test("a step with no description shows its stand-in, marked not written yet", async ({ page }) => {
    await open(page, "/workflows?id=publish-fix");
    const seg = DESCRIBED["publish-fix"].paragraph.segments.find((s) => s.standin && s.part === "step")!;
    expect(seg).toBeTruthy();
    const stand = paragraph(page).locator(`[data-step="${seg.step}"] .sv-standin`);
    // The words are the API's own stand-in, as paragraph.text reads them.
    await expect(stand).toHaveText(`${seg.shown} (not written yet)`);
    expect(DESCRIBED["publish-fix"].paragraph.text).toContain(String(seg.shown));
    expect(await stand.evaluate((el) => getComputedStyle(el).fontStyle)).toBe("italic");
    // Clicking it opens the step's panel (d14) with an empty description for the author to write fresh.
    await stand.click();
    const panel = page.getByRole("dialog", { name: /^Edit / });
    await expect(panel.getByRole("textbox", { name: "One-line description" })).toHaveValue("");
    await expect(stand).toHaveClass(/sv-selected/);
  });

  test("a workflow with no description shows the gap on its card and in Simple", async ({ page }) => {
    await open(page, "/workflows?id=daily-newspaper");
    await expect(page.getByLabel("Workflow description").locator(".sv-gap")).toHaveText("No description yet");
    await expect(paragraph(page)).toBeVisible();
  });

  test("d14: a step opens its panel, a rule opens its editor with the enable switch; the selected words are highlighted", async ({ page }) => {
    await open(page, "/workflows?id=pr-fix");
    const step = paragraph(page).locator(".sv-step .sv-text").nth(1);
    await step.click();
    const panel = page.getByRole("dialog", { name: /^Edit / });
    await expect(panel.getByRole("textbox", { name: "One-line description" })).toBeVisible();
    await expect(paragraph(page).locator(".sv-selected")).toHaveCount(1);
    await page.keyboard.press("Escape");
    await expect(panel).toHaveCount(0);
    await expect(step).toBeFocused();
    const rule = paragraph(page).locator("[data-rule] .sv-text").first();
    await rule.click({ position: { x: 24, y: 8 } });
    const editor = page.getByRole("dialog", { name: "Rule editor" });
    await expect(editor.getByRole("switch", { name: /enabled/i }).first()).toBeVisible();
    await expect(rule).toHaveClass(/sv-selected/);
    await editor.getByRole("button", { name: "Close" }).click();
    await expect(editor).toHaveCount(0);
    await expect(rule).toBeFocused();
  });

  test("d13: disabled rules read muted with a tag, not struck through; Simple has no legend", async ({ page }) => {
    await open(page, "/workflows?id=pr-fix");
    const pill = paragraph(page).locator(".sv-pill").first();
    await expect(pill).toHaveText("disabled");
    expect(await paragraph(page).locator(".sv-disabled").first().evaluate((el) => getComputedStyle(el).textDecorationLine)).not.toBe("line-through");
    await expect(page.getByText(/same machine|crosses machines/)).toHaveCount(0);
  });
});
