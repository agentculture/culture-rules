/**
 * Fold parity (#69 t7): the Python paragraph builder (culture_rules/model/paragraph.py
 * fold_rules) mirrors foldModel to decide which rules start a workflow and which continue
 * into it. Both sides are checked against one committed fixture of the docs/rules corpus:
 * this test (TS) and tests/model/test_rule_paragraph.py (Python). Regenerate the fixture
 * from this side with FOLD_PARITY_WRITE=1 npx vitest run src/fold/parity.test.ts.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import type { Rule, Workflow } from "../api/types";
import { foldModel } from "./model";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const FIXTURE = resolve(ROOT, "tests/fixtures/fold_docs_rules.json");

/** Every committed bundle's files of one kind, sorted by bundle then file name (as Python reads them). */
function corpus<T>(kind: "rules" | "workflows"): T[] {
  const docs = resolve(ROOT, "docs/rules");
  return readdirSync(docs).sort().flatMap((bundle) => {
    const dir = resolve(docs, bundle, kind);
    let names: string[];
    try {
      names = readdirSync(dir);
    } catch {
      return [];
    }
    return names.filter((n) => n.endsWith(".json")).sort()
      .map((n) => JSON.parse(readFileSync(resolve(dir, n), "utf8")) as T);
  });
}

function snapshot() {
  const model = foldModel(corpus<Rule>("rules"), corpus<Workflow>("workflows"));
  const entries = model.workflows.flatMap((w) => w.entries);
  const order = new Map(corpus<Rule>("rules").map((r, i) => [r.id, i]));
  entries.sort((a, b) => order.get(a.rule.id)! - order.get(b.rule.id)!);
  return {
    source: "web/src/fold/model.ts foldModel over docs/rules (web/src/fold/parity.test.ts)",
    entries: entries.map((e) => ({
      rule: e.rule.id,
      workflow: e.workflowId,
      kind: e.kind,
      enabled: e.enabled,
      predecessor: e.kind === "continuation"
        ? (e.predecessor.kind === "linked" ? { kind: "linked", workflow_id: e.predecessor.workflowId }
          : e.predecessor.kind === "ambiguous" ? { kind: "ambiguous", workflow_ids: e.predecessor.workflowIds }
            : { kind: "any" })
        : null,
      from: e.kind === "continuation" ? e.fromWorkflowId : null,
    })),
  };
}

describe("fold parity with the Python paragraph builder", () => {
  it("folds the committed docs/rules corpus exactly as the committed fixture says", () => {
    const got = snapshot();
    if (process.env.FOLD_PARITY_WRITE === "1") writeFileSync(FIXTURE, `${JSON.stringify(got, null, 2)}\n`);
    expect(got).toEqual(JSON.parse(readFileSync(FIXTURE, "utf8")));
  });
});
