import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Page } from "@playwright/test";
import type { Rule } from "../../src/api/types";
import type { Step, WorkflowDef } from "../../src/api/workflows";
import { mockApi } from "./api";
import { mockWorkflowsApi, type Recorded } from "./workflows";

/**
 * The ten committed workflows (docs/rules) and their rules with the written descriptions
 * applied, and what the real API answers for each `GET /workflows/{id}/describe`. The JSON is
 * generated from Python (tests/model/test_describe_paragraphs_fixture.py regenerates it and fails
 * on drift), so the mock serves the paragraph exactly as `workflow_paragraph` builds it.
 * `daily-newspaper` has no description of its own and `publish-fix` a step with none.
 */
export interface Segment {
  part: string;
  text: string;
  phrase?: string;
  step?: string;
  name?: string;
  gap?: boolean;
  fallback?: boolean;
  disabled?: boolean;
  [key: string]: unknown;
}
export interface DescribedAnswer {
  id: string;
  kind: "workflow";
  description: string;
  lines: string[];
  entries: unknown[];
  paragraph: { segments: Segment[]; text: string };
}

const FILE = resolve(dirname(fileURLToPath(import.meta.url)), "describe-paragraphs.json");
const FIXTURE = JSON.parse(readFileSync(FILE, "utf8")) as {
  rules: Rule[];
  workflows: Record<string, WorkflowDef>;
  describe: Record<string, DescribedAnswer>;
};

export const DESCRIBED_RULES = FIXTURE.rules;
export const DESCRIBED_WORKFLOWS = Object.values(FIXTURE.workflows);
export const DESCRIBED = FIXTURE.describe;

/** Every step id of a workflow in declared order, a loop's body after the loop. */
export function stepIds(steps: readonly Step[] | undefined): string[] {
  return (steps ?? []).flatMap((s) => [s.id, ...stepIds(s.body)]);
}

/** Compose after nothing: serves the shared API, the ten workflows, their rules and describes. */
export async function mockDescribedApi(page: Page): Promise<Recorded[]> {
  await mockApi(page);
  const calls = await mockWorkflowsApi(page, DESCRIBED_WORKFLOWS);
  const json = (data: unknown) => ({ status: 200, contentType: "application/json", body: JSON.stringify(data) });
  // Registered later, so these win over mockWorkflowsApi's stand-ins.
  await page.route("**/api/rules", (route) =>
    route.request().method() === "GET" ? route.fulfill(json({ items: DESCRIBED_RULES })) : route.fallback(),
  );
  await page.route(/\/api\/workflows\/[^/]+\/describe$/, (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split("/").at(-2)!);
    const answer = DESCRIBED[id];
    return answer
      ? route.fulfill(json(answer))
      : route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ error: { code: "not_found", message: id, errors: [] } }) });
  });
  return calls;
}
