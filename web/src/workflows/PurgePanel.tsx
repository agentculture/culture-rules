import { useEffect, useRef } from "react";
import { ApiError } from "../api/client";
import GuidedNotice from "../components/GuidedNotice";
import type { WorkflowDef } from "./../api/workflows";

export interface PurgeState {
  workflow: WorkflowDef;
  /** The dry run answered: the purge is ready to be confirmed. */
  checked: boolean;
  busy: boolean;
  error: ApiError | null;
}

interface Props {
  state: PurgeState;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * The confirmation step of an admin purge (a non-modal panel, never
 * `window.confirm`, which blocks automation). It opens after the dry run
 * (`POST /workflows/{id}/purge {apply:false}`); only "Confirm purge" sends
 * `{apply:true}`. Failures are guided text, never the server's raw message.
 */
export default function PurgePanel({ state, onConfirm, onCancel }: Readonly<Props>) {
  const ref = useRef<HTMLElement>(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  const { workflow, checked, busy, error } = state;
  return (
    <section ref={ref} tabIndex={-1} className="wf-purge" aria-label={`Purge ${workflow.name}`}>
      <h2 className="wf-purge__title">Purge {workflow.name}?</h2>
      {error ? (
        <GuidedNotice error={error} />
      ) : (
        <p className="wf-purge__text">
          {checked
            ? `This permanently removes ${workflow.name} and its history. It cannot be restored.`
            : "Checking what a purge would remove…"}
        </p>
      )}
      <div className="wf-purge__actions">
        <button
          type="button"
          className="wf-button wf-button--large wf-purge__confirm"
          disabled={!checked || busy || error !== null}
          onClick={onConfirm}
        >
          Confirm purge
        </button>
        <button type="button" className="wf-button wf-button--large" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </section>
  );
}
