import type { StopOffer } from "./useRulesData";

const runs = (n: number) => `${n} current run${n === 1 ? "" : "s"}`;

interface Props {
  offer: StopOffer;
  onApprove: () => void;
  onDismiss: () => void;
}

/**
 * d17: after a rule is disabled with runs still going, a non-modal notice asks
 * 'Stop N current runs?'. Approve cancels them (`POST /rules/{id}/stop-runs`)
 * and reports how many stopped; Keep running dismisses it and the runs go on
 * (a push step still refuses, since the rule is off). It never takes focus:
 * the toggle keeps it, and the buttons are next in the tab order.
 */
export function StopRunsNotice({ offer, onApprove, onDismiss }: Readonly<Props>) {
  if (offer.phase === "done") {
    const n = offer.stopped ?? 0;
    return (
      <output className="notice notice--undo notice--stop" data-stop-phase="done">
        <span>
          Stopped {n} run{n === 1 ? "" : "s"} of {offer.rule.name}.
        </span>
        <button type="button" className="icon-button icon-button--small" aria-label="Dismiss" onClick={onDismiss}>
          ×
        </button>
      </output>
    );
  }
  const busy = offer.phase === "busy";
  return (
    <output className="notice notice--undo notice--stop" data-stop-phase={offer.phase}>
      <span>
        {offer.rule.name} is off. Stop {runs(offer.total)}?
      </span>
      <button
        type="button"
        className="btn btn--primary"
        aria-label={`Approve: stop ${runs(offer.total)} of ${offer.rule.name}`}
        disabled={busy}
        onClick={onApprove}
      >
        {busy ? "Stopping…" : "Approve"}
      </button>
      <button type="button" className="btn" disabled={busy} onClick={onDismiss}>
        Keep running
      </button>
    </output>
  );
}
