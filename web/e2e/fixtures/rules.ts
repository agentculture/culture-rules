import type { Page } from "@playwright/test";
import { createFakeApi, handle, type FakeApi } from "../../src/rules/fake-api";

/**
 * A stateful culture-rules API for the Rules tab's e2e: the in-memory fake
 * (`src/rules/fake-api.ts`, api/openapi.json shapes) behind request
 * interception, so a toggle, an edit or a relationship drop is really sent
 * and the next read sees it, `GET /asks` included, so ask answering is
 * exercised end to end.
 */
export async function mockRulesApi(page: Page, api: FakeApi = createFakeApi()): Promise<FakeApi> {
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const raw = request.postData();
    const res = handle(
      api,
      request.method(),
      url.pathname.replace(/^\/api/, ""),
      url.searchParams,
      raw ? JSON.parse(raw) : undefined,
    );
    await route.fulfill({
      status: res.status,
      contentType: "application/json",
      body: JSON.stringify(res.body),
    });
  });
  return api;
}
