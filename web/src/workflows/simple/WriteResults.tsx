import { ApiError } from "../../api/client";
import { failureMessage } from "../../api/settle";
import type { FoldWriteResult, WriteBatch } from "./useFanout";

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : failureMessage(err));

function Words({ result, batch }: Readonly<{ result: FoldWriteResult; batch: WriteBatch }>) {
  if (result.status === "saved") return <span className="fold-result__words">saved</span>;
  if (result.status === "unchanged") return <span className="fold-result__words">already so; nothing to write</span>;
  if (result.status === "skipped-changed") {
    return (
      <span className="fold-result__words">
        not saved: changed since you opened it, so it was left alone. It is now {batch.oldText(result.current)}.
      </span>
    );
  }
  const when = result.phase === "read" ? "could not be re-read" : result.phase === "prepare" ? "cannot take this edit" : "not saved";
  return (
    <span className="fold-result__words">
      {when === "not saved" ? "not saved" : `not saved: ${when}`} ({errorText(result.error)}); keeps {batch.oldText(result.snapshot)}
    </span>
  );
}

/**
 * What a write through the fold writes did, rule by rule (spec c25, c27):
 * saved, failed (with the old value the rule keeps and a retry), or skipped
 * because the rule changed after it was shown (re-applied only on request).
 * Shown after every shared edit, and after a single entry's write that did
 * not land.
 */
export function WriteResults({
  batch,
  busy,
  onRetry,
  onDismiss,
}: Readonly<{ batch: WriteBatch; busy: boolean; onRetry: (ruleId: string) => void; onDismiss: () => void }>) {
  const saved = batch.results.filter((r) => r.status === "saved" || r.status === "unchanged").length;
  const total = batch.results.length;
  const allSaved = saved === total;
  return (
    <section className={`fold-results${allSaved ? "" : " fold-results--partial"}`} aria-label="Save results" aria-live="polite">
      <div className="fold-results__head">
        <span className="fold-results__title">
          {batch.label}: {allSaved ? `done for ${total === 1 ? "its rule" : `all ${total} rules`}` : `${saved} of ${total} rules done`}
        </span>
        <button type="button" className="icon-button icon-button--small" aria-label="Dismiss save results" onClick={onDismiss}>
          ×
        </button>
      </div>
      <ul className="fold-results__list">
        {batch.results.map((result) => {
          const name = result.snapshot.name;
          return (
            <li key={result.ruleId} className="fold-result" data-status={result.status}>
              <span className={`run-dot run-dot--${result.status === "saved" || result.status === "unchanged" ? "succeeded" : "failed"}`} aria-hidden="true" />
              <span className="fold-result__name">{name}</span>
              <Words result={result} batch={batch} />
              {result.status === "failed" ? (
                <button type="button" className="btn" disabled={busy} aria-label={`Retry ${name}`} onClick={() => onRetry(result.ruleId)}>
                  Retry
                </button>
              ) : null}
              {result.status === "skipped-changed" ? (
                <button
                  type="button"
                  className="btn"
                  disabled={busy}
                  aria-label={`Apply to ${name} as it is now`}
                  onClick={() => onRetry(result.ruleId)}
                >
                  Apply to it as it is now
                </button>
              ) : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
