import { useCallback, useRef, useState } from "react";
import type { Rule } from "../../api/types";
import { predecessorTerms } from "../../fold/model";
import { saveSharedEdit, savePredecessor, type RuleWriteResult, type SharedRuleEdit } from "../../fold/writes";
import { fieldOf, valueText } from "./text";

export type FoldWriteResult = RuleWriteResult;

/** Write `edit` to one rule through the fold writes (re-read first; a no-op is `unchanged` there). */
async function writeOne(snapshot: Rule, edit: SharedRuleEdit): Promise<FoldWriteResult> {
  return (await saveSharedEdit([snapshot], edit))[0];
}

/**
 * One write the Simple view made through the fold writes (web/src/fold/writes.ts):
 * a shared edit fanned out to every entry point's rule, or a single entry's
 * attempt-counting or predecessor change. The results stay on screen so a
 * partial failure is never silent: each failed rule keeps its old value and
 * offers a retry, a rule changed meanwhile is flagged and left alone.
 */
export interface WriteBatch {
  id: number;
  /** What was saved, in words: "Ends here", "Runs", "Review asked for changes: predecessor". */
  label: string;
  results: FoldWriteResult[];
  /** The old value a rule keeps when its write did not land, in words. */
  oldText: (rule: Rule) => string;
  /** Write it again for one rule, from this snapshot. */
  redo: (snapshot: Rule) => Promise<FoldWriteResult>;
}

/** Old-value words for the fields a shared edit replaces ("pr-fixer:… , 3 attempts per key"). */
export function fieldsText(fields: readonly string[]) {
  return (rule: Rule) => fields.map((field) => valueText(field, fieldOf(rule, field))).join(", ");
}

export function useFanout(onWritten: () => void) {
  const [batch, setBatch] = useState<WriteBatch | null>(null);
  const [busy, setBusy] = useState(false);
  const next = useRef(0);

  const start = useCallback(
    async (label: string, oldText: WriteBatch["oldText"], redo: WriteBatch["redo"], write: () => Promise<FoldWriteResult[]>) => {
      setBusy(true);
      try {
        const results = await write();
        next.current += 1;
        setBatch({ id: next.current, label, results, oldText, redo });
        return results;
      } finally {
        setBusy(false);
        onWritten();
      }
    },
    [onWritten],
  );

  /**
   * A per-rule edit: each rule's write is computed from that rule (its own current condition,
   * say), so a retry or "apply to it as it is now" recomputes it from the rule as stored then.
   */
  const fanOutEach = useCallback(
    (label: string, fields: readonly string[], snapshots: readonly Rule[], editFor: (rule: Rule) => SharedRuleEdit) => {
      const one = (snapshot: Rule) => writeOne(snapshot, editFor(snapshot));
      // Every input is captured before the first await, so UI changes cannot alter a batch.
      const all = snapshots.map((s) => structuredClone(s));
      return start(label, fieldsText(fields), one, async () => {
        const results: FoldWriteResult[] = [];
        for (const snapshot of all) results.push(await one(snapshot));
        return results;
      });
    },
    [start],
  );
  /** A shared edit: every snapshot's rule, one at a time, re-read before each write. */
  const fanOut = useCallback(
    (label: string, snapshots: readonly Rule[], edit: SharedRuleEdit) =>
      fanOutEach(label, Object.keys(edit), snapshots, () => edit),
    [fanOutEach],
  );


  /** A continuation's predecessor: exactly its data.workflow_id compare is rewritten. */
  const predecessor = useCallback(
    (label: string, snapshot: Rule, workflowId: string) =>
      start(
        label,
        (rule) => `continues from ${predecessorText(rule)}`,
        (fresh) => savePredecessor(fresh, workflowId),
        async () => [await savePredecessor(snapshot, workflowId)],
      ),
    [start],
  );

  /** Retry a failed rule from its snapshot; re-apply a skipped one onto its current version. */
  const retry = useCallback(
    async (ruleId: string) => {
      const current = batch;
      const result = current?.results.find((r) => r.ruleId === ruleId);
      if (!current || !result || result.status === "saved" || result.status === "unchanged") return;
      setBusy(true);
      try {
        const redone = await current.redo(result.status === "skipped-changed" ? result.current : result.snapshot);
        setBatch((b) => (b?.id === current.id
          ? { ...b, results: b.results.map((r) => (r.ruleId === ruleId ? redone : r)) }
          : b));
      } finally {
        setBusy(false);
        onWritten();
      }
    },
    [batch, onWritten],
  );

  const dismiss = useCallback(() => setBatch(null), []);
  return { batch, busy, fanOut, fanOutEach, predecessor, retry, dismiss };
}

/** The workflow a rule's single predecessor term names, as the fold model reads it. */
function predecessorText(rule: Rule): string {
  const terms = predecessorTerms(rule.condition);
  if (terms.length !== 1) return "its previous workflow";
  const [term] = terms;
  const literal = "literal" in term.right ? term.right.literal : "literal" in term.left ? term.left.literal : null;
  return typeof literal === "string" ? literal : "its previous workflow";
}
