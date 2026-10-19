import { useCallback, useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { repointWorkflowActor, type Actor } from "../api/actors";
import { ApiError } from "../api/client";
import type { ActorReference } from "../api/types";
import { REVISION_CONFLICT_MESSAGE, getWorkflowDef, isRevisionConflict } from "../api/workflows";

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

/** Where a referrer opens: a workflow on the Workflows tab, a rule through its redirect. */
export const referrerHref = (kind: string, id: string) =>
  kind === "rule" ? `/rules/${encodeURIComponent(id)}` : `/workflows?id=${encodeURIComponent(id)}`;

interface Group {
  kind: string;
  id: string;
  paths: string[];
  /** A step action names the actor, so a Move can repoint it. */
  movable: boolean;
}

/** One entry per workflow or rule, its step paths together, in the order the server listed them. */
function grouped(items: readonly ActorReference[]): Group[] {
  const out = new Map<string, Group>();
  for (const ref of items) {
    const key = `${ref.kind}/${ref.id}`;
    const group = out.get(key) ?? { kind: ref.kind, id: ref.id, paths: [], movable: false };
    group.paths.push(ref.path);
    group.movable ||= ref.kind === "workflow" && ref.via === "action";
    out.set(key, group);
  }
  return [...out.values()];
}

type MoveState =
  | { phase: "reading" }
  | { phase: "open"; revision: number | null }
  | { phase: "conflict"; revision: number | null }
  | { phase: "trusted"; revision: number | null; message: string }
  | { phase: "error"; revision: number | null; message: string };

/**
 * Move one workflow off this actor: pick the actor its steps should name instead. The
 * workflow's revision is read when the form opens, and the repoint sends it, so a workflow
 * that changed meanwhile is refused with the same words and Reload the workflow editor uses.
 */
function MoveForm({
  actor,
  workflowId,
  targets,
  onMoved,
  onCancel,
}: Readonly<{
  actor: Actor;
  workflowId: string;
  targets: Actor[];
  onMoved: (message: string) => void;
  onCancel: () => void;
}>) {
  const uid = useId();
  const [to, setTo] = useState(targets[0]?.id ?? "");
  const [state, setState] = useState<MoveState>({ phase: "reading" });
  const [busy, setBusy] = useState(false);

  const read = useCallback(async () => {
    setState({ phase: "reading" });
    try {
      const def = await getWorkflowDef(workflowId);
      setState({ phase: "open", revision: def.revision ?? null });
    } catch (err) {
      setState({ phase: "error", revision: null, message: errorText(err) });
    }
  }, [workflowId]);

  // Read the revision once, as the form opens.
  useEffect(() => {
    void read();
  }, [read]);

  const revision = state.phase === "reading" ? null : state.revision;

  const move = async (force: boolean) => {
    setBusy(true);
    try {
      const answer = await repointWorkflowActor(workflowId, {
        from: actor.id,
        to,
        expected_revision: revision,
        force,
      });
      const name = targets.find((t) => t.id === to)?.name ?? to;
      onMoved([`Moved ${workflowId} to ${name}.`, ...answer.warnings].join(" "));
    } catch (err) {
      if (isRevisionConflict(err)) setState({ phase: "conflict", revision });
      else if (err instanceof ApiError && err.code === "workflow_trusted") {
        setState({ phase: "trusted", revision, message: err.message });
      } else setState({ phase: "error", revision, message: errorText(err) });
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void move(false);
  };

  return (
    <form className="used-by__move" onSubmit={submit} aria-label={`Move ${workflowId}`}>
      <label htmlFor={`${uid}-to`} className="used-by__to">
        To
      </label>
      <select id={`${uid}-to`} value={to} onChange={(e) => setTo(e.target.value)}>
        {targets.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      <button type="submit" className="btn btn--primary" disabled={busy || !to || state.phase === "reading"}>
        Move
      </button>
      <button type="button" className="btn" onClick={onCancel}>
        Cancel
      </button>
      {state.phase === "conflict" ? (
        <p className="notice notice--error used-by__notice" role="alert">
          {REVISION_CONFLICT_MESSAGE}.{" "}
          <button type="button" className="btn" onClick={() => void read()}>
            Reload
          </button>
        </p>
      ) : null}
      {state.phase === "trusted" ? (
        <p className="notice notice--error used-by__notice" role="alert">
          {state.message}{" "}
          <button type="button" className="btn btn--danger" disabled={busy} onClick={() => void move(true)}>
            Move anyway
          </button>
        </p>
      ) : null}
      {state.phase === "error" ? (
        <p className="notice notice--error used-by__notice" role="alert">
          {state.message}
        </p>
      ) : null}
    </form>
  );
}

/**
 * Used by (t10): the live workflows and rules naming the actor, each linking where it is
 * edited. An editor can Move a workflow's steps to another actor; a rule's placement is
 * changed on its workflow.
 */
export function UsedBy({
  actor,
  items,
  error,
  actors,
  canMove,
  onMoved,
}: Readonly<{
  actor: Actor;
  /** null while reading. */
  items: ActorReference[] | null;
  error: string | null;
  actors: Actor[];
  canMove: boolean;
  onMoved: () => void;
}>) {
  const [moving, setMoving] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const targets = actors.filter((a) => a.id !== actor.id && a.enabled !== false);
  const groups = items ? grouped(items) : [];

  return (
    <section className="used-by" aria-label={`${actor.name} used by`}>
      <h2 className="used-by__title">Used by</h2>
      {error ? <p className="notice notice--error">{error}</p> : null}
      {items && groups.length === 0 ? <p className="muted used-by__none">Nothing uses {actor.name}.</p> : null}
      {groups.length ? (
        <ul className="used-by__list">
          {groups.map((g) => (
            <li key={`${g.kind}/${g.id}`} className="used-by__item">
              <div className="used-by__row">
                <span className="used-by__kind muted">{g.kind}</span>
                <Link className="used-by__link" to={referrerHref(g.kind, g.id)}>
                  {g.id}
                </Link>
                <span className="mono muted used-by__paths">{g.paths.join(", ")}</span>
                {canMove && g.movable && moving !== g.id ? (
                  <button
                    type="button"
                    className="btn used-by__move-button"
                    aria-label={`Move ${g.id}`}
                    onClick={() => {
                      setDone(null);
                      setMoving(g.id);
                    }}
                  >
                    Move
                  </button>
                ) : null}
              </div>
              {moving === g.id ? (
                <MoveForm
                  actor={actor}
                  workflowId={g.id}
                  targets={targets}
                  onCancel={() => setMoving(null)}
                  onMoved={(message) => {
                    setMoving(null);
                    setDone(message);
                    onMoved();
                  }}
                />
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {done ? <output className="used-by__done">{done}</output> : null}
    </section>
  );
}

export default UsedBy;
