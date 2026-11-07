import type { Page } from "@playwright/test";
import { createFakeApi, handle, type FakeApi } from "../../src/rules/fake-api";
import { mockRulesApi } from "./rules";

import { FOLD_RULES, FOLD_WORKFLOWS } from "../../src/workflows/simple/fixture";

/** A self-contained chain shared with the editor unit tests. */
export const DOCUMENT_CHAIN_RULES = FOLD_RULES;
export const DOCUMENT_CHAIN_WORKFLOWS = FOLD_WORKFLOWS;

/** The stateful fake API (src/rules/fake-api.ts) holding the Document chain, served under `/api`. */
export async function mockDocumentChainApi(page: Page): Promise<FakeApi> {
  const api = createFakeApi(Date.parse("2026-10-09T12:00:00Z"));
  api.rules = structuredClone(DOCUMENT_CHAIN_RULES);
  api.workflows = structuredClone(DOCUMENT_CHAIN_WORKFLOWS);
  // Listed once, so every rule carries the `updated_at` a fan-out snapshot compares.
  handle(api, "GET", "/rules", new URLSearchParams());
  api.calls = [];
  return mockRulesApi(page, api);
}

/** The writes the page sent (every non-GET call), as `METHOD /path`. */
export const writesOf = (api: FakeApi) =>
  api.calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);
