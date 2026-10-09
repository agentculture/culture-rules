import { useEffect, useState } from "react";
import { ApiError } from "../../api/client";
import { getRuleHistory, type RuleHistoryItem } from "../../api/rules";
import { ago } from "../../routes/rules-view";

const HISTORY_LIMIT = 6;

const SKIP_WORDS: Record<string, string> = {
  superseded_by: "superseded by",
  group_lost: "lost its group to",
  blocked_by_predecessor: "waiting for",
};

type Loaded = { ruleId: string; items: RuleHistoryItem[]; error: string | null };

/**
 * One entry point's last runs and recorded skips, newest first: the Rules
 * tab's 'Last runs' column, read from the same `GET /rules/{id}/history`
 * (spec c29). `tick` re-reads it (a live change to runs or decisions).
 */
export function EntryHistory({
  ruleId,
  nameOf,
  tick,
}: Readonly<{ ruleId: string; nameOf: (id: string) => string; tick: number }>) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getRuleHistory(ruleId, HISTORY_LIMIT, controller.signal)
      .then((items) => setLoaded({ ruleId, items, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded({ ruleId, items: [], error: err instanceof ApiError ? err.message : String(err) });
      });
    return () => controller.abort();
  }, [ruleId, tick]);

  const mine = loaded?.ruleId === ruleId ? loaded : null;
  const now = Date.now();
  let rows;
  if (!mine) rows = <li className="fold-history__note">Reading…</li>;
  else if (mine.error) rows = <li className="fold-history__note fold-history__note--error">{mine.error}</li>;
  else if (mine.items.length === 0) rows = <li className="fold-history__note">No runs yet</li>;
  else {
    rows = mine.items.map((item) =>
      item.kind === "decision" ? (
        <li key={`d-${item.event_id}`} className="fold-history__item" data-decision={item.reason}>
          <span className="fold-history__when">skipped · {ago(item.at, now)}</span>
          <span>{SKIP_WORDS[item.reason] ? `${SKIP_WORDS[item.reason]} ${item.by.map(nameOf).join(", ")}` : (item.message ?? item.reason)}</span>
        </li>
      ) : (
        <li key={item.id} className="fold-history__item" data-run-status={item.status}>
          <span className={`run-dot run-dot--${item.status}`} aria-hidden="true" />
          <span>{item.status}</span>
          <span className="fold-history__when">{ago(item.created_at, now)}</span>
        </li>
      ),
    );
  }
  return (
    <ul className="fold-history" aria-label="Last runs">
      {rows}
    </ul>
  );
}
