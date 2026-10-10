import { readFileSync, readdirSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import type { Rule } from "../../src/api/types";
import type { WorkflowDef } from "../../src/api/workflows";
import { createFakeApi, handle, type FakeApi } from "../../src/rules/fake-api";
import { mockRulesApi } from "./rules";

/**
 * The PR fixer as it is stored (docs/rules/pr-fixer/{rules,workflows}/*.json),
 * read from disk so the fold's counts are computed from the real rule set,
 * never hard-coded (spec h-count): since d31 18 rules, 6 workflows, 9 entry
 * points and 9 continuations in 2 chains (deviation d2 counted 9, 4, 6 and 3).
 */
const DOCS = resolve(dirname(fileURLToPath(import.meta.url)), "../../../docs/rules/pr-fixer");

function readAll<T>(directory: string): T[] {
  const path = resolve(DOCS, directory);
  return readdirSync(path)
    .filter((name) => name.endsWith(".json"))
    .sort()
    .map((name) => JSON.parse(readFileSync(resolve(path, name), "utf8")) as T);
}

export const PR_FIXER_RULES: Rule[] = readAll<Rule>("rules");
export const PR_FIXER_WORKFLOWS: WorkflowDef[] = readAll<WorkflowDef>("workflows");

/** The stateful fake API (src/rules/fake-api.ts) holding the PR fixer, served under `/api`. */
export async function mockPrFixerApi(page: Page): Promise<FakeApi> {
  const api = createFakeApi(Date.parse("2026-10-09T12:00:00Z"));
  api.rules = structuredClone(PR_FIXER_RULES);
  api.workflows = structuredClone(PR_FIXER_WORKFLOWS);
  // Listed once, so every rule carries the `updated_at` a fan-out snapshot compares.
  handle(api, "GET", "/rules", new URLSearchParams());
  api.calls = [];
  return mockRulesApi(page, api);
}

/** The writes the page sent (every non-GET call), as `METHOD /path`. */
export const writesOf = (api: FakeApi) =>
  api.calls.filter((c) => c.method !== "GET").map((c) => `${c.method} ${c.path}`);
