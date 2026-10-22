import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  createActor,
  deleteActor,
  disableActor,
  getActorReferences,
  inUseReferrers,
  isMissingRoute,
  listActors,
  setActorEnabled,
  updateActor,
  type Actor,
  type InUseReferrer,
} from "../api/actors";
import { ApiError, listMachines } from "../api/client";
import GuidedNotice from "../components/GuidedNotice";
import { settleAll } from "../api/settle";
import type { ActorReference, Machine } from "../api/types";
import { useWhoami } from "../hooks/useWhoami";
import { setAgentState } from "../agent-state/store";
import { machineColors } from "../culture-design/chart";
import { MachineDot, Switch, machineStyle } from "../culture-design/stages";
import { usePending } from "../usePending";
import { useTabReady } from "../routes/useTabReady";
import { ActorForm } from "./ActorForm";
import { AddModelsPanel } from "./AddModelsPanel";
import { UsedBy, referrerHref } from "./UsedBy";
import { FILTERS, configSourceText, filterActors, kindOfFilter, type KindFilter } from "./actors-view";
import "./actors.css";

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

const PencilIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 20h4L19 9l-4-4L4 16v4z" />
  </svg>
);
const TrashIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
  </svg>
);

/**
 * The repo | db radio, a single tab stop (roving tabindex on the radios) with
 * arrow-key movement; focus follows the checked radio.
 */
function SourceSwitch({
  value,
  onChange,
}: Readonly<{ value: "repo" | "db"; onChange: (v: "repo" | "db") => void }>) {
  const options = ["repo", "db"] as const;
  const refs = useRef<Partial<Record<"repo" | "db", HTMLButtonElement | null>>>({});
  const onKey = (event: KeyboardEvent) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const next = value === "repo" ? "db" : "repo";
    onChange(next);
    refs.current[next]?.focus();
  };
  return (
    <div className="source-switch" role="radiogroup" aria-label="Configuration source">
      {options.map((option) => (
        <button
          key={option}
          ref={(el) => {
            refs.current[option] = el;
          }}
          type="button"
          role="radio"
          aria-checked={value === option}
          tabIndex={value === option ? 0 : -1}
          className="source-switch__option"
          onClick={() => value !== option && onChange(option)}
          onKeyDown={onKey}
        >
          {option}
        </button>
      ))}
    </div>
  );
}

/** An actor's Used by answer, tagged with the actor it belongs to. */
interface References {
  actorId: string;
  items: ActorReference[] | null;
  error: string | null;
}

/**
 * Whether this server has the model-actor routes (Add models, Used by, Move, the in-use
 * refusal): learned from the first `GET /actors/{id}/references` answer. A missing route
 * (an older server, decision c33) hides those controls.
 */
type ModelRoutes = "unknown" | "present" | "missing";

/** A refused disable: the workflows and rules still naming the actor. */
function InUseRefusal({
  actor,
  referrers,
  busy,
  onForce,
  onKeep,
}: Readonly<{
  actor: Actor;
  referrers: InUseReferrer[];
  busy: boolean;
  onForce: () => void;
  onKeep: () => void;
}>) {
  return (
    <div className="actor-inuse" role="alert">
      <p className="actor-inuse__text">
        {actor.name} is still used by{" "}
        {referrers.map((r, i) => (
          <span key={`${r.kind}/${r.id}`}>
            {i > 0 ? ", " : null}
            {r.kind === "rule" ? "rule " : null}
            <Link to={referrerHref(r.kind, r.id)}>{r.id}</Link>
          </span>
        ))}
        . Move them to another actor first, or disable it anyway.
      </p>
      <span className="actor-inuse__actions">
        <button type="button" className="btn btn--danger" disabled={busy} onClick={onForce}>
          Disable anyway
        </button>
        <button type="button" className="btn" onClick={onKeep}>
          Keep enabled
        </button>
      </span>
    </div>
  );
}

/** The name, kind, machine and enable switch that head an actor's row. */
function ActorHead({
  actor,
  slot,
  enabled,
  open,
  pending,
  onSelect,
  onToggle,
}: Readonly<{
  actor: Actor;
  slot: number | null;
  enabled: boolean;
  open: boolean;
  pending: boolean;
  onSelect: () => void;
  onToggle: (next: boolean) => void;
}>) {
  return (
    <div className="actor-row__head">
      <button
        type="button"
        className="actor-row__name"
        aria-expanded={open}
        onClick={onSelect}
      >
        {actor.name}
      </button>
      <span className="actor-row__kind">{actor.kind}</span>
      <span className="actor-row__machine">
        {actor.machine ? (
          <>
            <MachineDot slot={slot} />
            {actor.machine}
          </>
        ) : (
          <span className="actor-row__anywhere">anywhere</span>
        )}
      </span>
      <Switch
        label={`${actor.name} enabled`}
        checked={enabled}
        disabled={pending}
        onChange={onToggle}
      />
    </div>
  );
}

/** Where the actor's config lives, plus its harness and model when it has them. */
function ActorConfigLine({
  actor,
  onSource,
}: Readonly<{ actor: Actor; onSource: (v: "repo" | "db") => void }>) {
  return (
    <div className="actor-row__config">
      <SourceSwitch
        value={actor.config_source === "repo" ? "repo" : "db"}
        onChange={onSource}
      />
      <span className="mono">{configSourceText(actor)}</span>
      {actor.harness ? (
        <span>
          <span className="muted">harness </span>
          <strong>{actor.harness}</strong>
        </span>
      ) : null}
      {actor.model ? (
        <span>
          <span className="muted">model </span>
          <strong>{actor.model}</strong>
        </span>
      ) : null}
    </div>
  );
}

/** The capability chips with the edit and delete controls, or the delete confirmation. */
function ActorCaps({
  actor,
  busy,
  confirming,
  onEdit,
  onAskDelete,
  onCancelDelete,
  onDelete,
}: Readonly<{
  actor: Actor;
  busy: boolean;
  confirming: boolean;
  onEdit: () => void;
  onAskDelete: () => void;
  onCancelDelete: () => void;
  onDelete: () => void;
}>) {
  return (
    <div className="actor-row__caps">
      <ul className="caps" aria-label="Capabilities">
        {(actor.capabilities ?? []).map((cap) => (
          <li key={cap} className="cap">
            {cap}
          </li>
        ))}
      </ul>
      {confirming ? (
        <fieldset className="actor-row__actions plain-group" aria-label={`Delete ${actor.name}?`}>
          <span className="confirm-text">Delete {actor.name}?</span>
          <button type="button" className="btn btn--danger" disabled={busy} onClick={onDelete}>
            Confirm delete
          </button>
          <button type="button" className="btn" onClick={onCancelDelete}>
            Cancel
          </button>
        </fieldset>
      ) : (
        <span className="actor-row__actions">
          <button
            type="button"
            className="icon-button"
            aria-label={`Edit ${actor.name}`}
            onClick={onEdit}
          >
            <PencilIcon />
          </button>
          <button
            type="button"
            className="icon-button icon-button--danger"
            aria-label={`Delete ${actor.name}`}
            onClick={onAskDelete}
          >
            <TrashIcon />
          </button>
        </span>
      )}
    </div>
  );
}

/**
 * The Actors tab — the 'Chosen — Actors' board (design canvas row 'Chosen'):
 * a large-type roster with a kind filter, one row per actor (name, kind,
 * machine in its color, enable switch). The selected actor (`?id=`, else the
 * first shown) expands inline with its configuration source, harness, model,
 * capabilities, edit and delete.
 */
export function ActorsBoard() {
  const [params, setParams] = useSearchParams();
  const [actors, setActors] = useState<Actor[] | null>(null);
  const [machines, setMachines] = useState<Machine[]>([]);
  const [loadErrors, setLoadErrors] = useState<string[]>([]);
  const [actionError, setActionError] = useState<string | null>(null);
  /** A refused save, shown as guided text (never the server's raw message). */
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [filter, setFilter] = useState<KindFilter>("all");
  /** The actor id being edited, the literal "new" for the create form, or null. */
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const whoami = useWhoami();
  const role = whoami.status === "signed-in" ? whoami.role : null;
  const [modelRoutes, setModelRoutes] = useState<ModelRoutes>("unknown");
  /** Once a route is known missing, stop asking (an older server never grows it mid-session). */
  const missingRef = useRef(false);
  const [references, setReferences] = useState<References | null>(null);
  const [refsVersion, setRefsVersion] = useState(0);
  const [addingModels, setAddingModels] = useState(false);
  /** A disable refused while the actor is in use: who still uses it. */
  const [inUse, setInUse] = useState<{ actorId: string; referrers: InUseReferrer[] } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    settleAll(
      [listActors(controller.signal), listMachines(controller.signal)],
      ([a, m]) => {
        if (controller.signal.aborted) return;
        setActors(a.status === "fulfilled" ? a.value : []);
        setMachines(m.status === "fulfilled" ? m.value : []);
        setLoadErrors(
          [a, m].flatMap((r) => (r.status === "rejected" ? [errorText(r.reason)] : [])),
        );
      },
      (message) => {
        // Applying the load failed: show an empty roster with the failure named.
        if (controller.signal.aborted) return;
        setActors([]);
        setMachines([]);
        setLoadErrors([message]);
      },
    );
    return () => controller.abort();
  }, []);

  const slots = useMemo(() => machineColors(machines.map((m) => m.name)), [machines]);
  const slotOf = (actor: Actor) =>
    actor.machine && slots.has(actor.machine) ? (slots.get(actor.machine) as number) : null;

  const all = actors ?? [];
  const shown = filterActors(all, filter);
  const requested = params.get("id");
  const selected = shown.find((a) => a.id === requested) ?? shown[0] ?? null;

  // Used by for the selected actor; the first answer also says whether the routes exist.
  // With no actor to ask about, a placeholder id still tells a real not_found from a
  // missing route.
  const probeId = actors === null ? null : selected?.id ?? "-";
  useEffect(() => {
    if (probeId === null || missingRef.current) return;
    const controller = new AbortController();
    getActorReferences(probeId, controller.signal).then(
      (answer) => {
        if (controller.signal.aborted) return;
        setModelRoutes("present");
        setReferences({ actorId: probeId, items: answer.items, error: null });
      },
      (err) => {
        if (controller.signal.aborted) return;
        if (isMissingRoute(err)) {
          missingRef.current = true;
          setModelRoutes("missing");
          setReferences(null);
          return;
        }
        setModelRoutes("present");
        const gone = err instanceof ApiError && err.status === 404;
        setReferences({ actorId: probeId, items: gone ? [] : null, error: gone ? null : errorText(err) });
      },
    );
    return () => controller.abort();
  }, [probeId, refsVersion]);

  const reloadActors = useCallback(() => {
    listActors().then(
      (next) => setActors(next),
      (err) => setActionError(errorText(err)),
    );
  }, []);

  const errors = [...loadErrors, ...(actionError ? [actionError] : [])];
  useTabReady("actors", actors !== null, errors);
  useEffect(() => {
    setAgentState({
      actors: actors
        ? { count: all.length, shown: shown.length, kind: filter, selected: selected?.id ?? null }
        : null,
    });
  }, [actors, all.length, shown.length, filter, selected?.id]);

  const select = (id: string) => {
    setEditing(null);
    setConfirming(null);
    setInUse(null);
    setParams({ id }, { replace: true });
  };

  /** Run a mutation: one at a time, its failure named in the alert, never swallowed. */
  const act = useCallback(async (work: () => Promise<void>, guided = false) => {
    setBusy(true);
    setActionError(null);
    setSaveError(null);
    try {
      await work();
    } catch (err) {
      // model_taken names the actor holding the model: the server's words say who.
      if (guided && err instanceof ApiError && err.code !== "model_taken") setSaveError(err);
      else setActionError(errorText(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const replace = (next: Actor) =>
    setActors((current) => (current ?? []).map((a) => (a.id === next.id ? next : a)));

  const { pending: togglePending, run: runToggle } = usePending();
  const toggle = (actor: Actor, enabled: boolean, force = false) =>
    runToggle(actor.id, () =>
      act(async () => {
        if (enabled || modelRoutes === "missing") {
          await setActorEnabled(actor.id, enabled);
        } else {
          try {
            await disableActor(actor.id, force);
          } catch (err) {
            const referrers = inUseReferrers(err);
            if (!referrers) throw err;
            setInUse({ actorId: actor.id, referrers });
            return;
          }
        }
        setInUse(null);
        replace({ ...actor, enabled });
      }),
    );

  const setSource = (actor: Actor, source: "repo" | "db") =>
    act(async () => {
      const saved = await updateActor({ ...actor, config_source: source });
      replace({ ...actor, ...saved, config_source: source });
    });

  const save = (actor: Actor, isNew: boolean) =>
    act(async () => {
      if (isNew) {
        const created = await createActor(actor);
        setActors((current) => [...(current ?? []), { ...actor, ...created }]);
        setFilter("all");
        setParams({ id: actor.id }, { replace: true });
      } else {
        const saved = await updateActor(actor);
        replace({ ...actor, ...saved });
      }
      setEditing(null);
    }, true);

  const remove = (actor: Actor) =>
    act(async () => {
      await deleteActor(actor.id);
      setActors((current) => (current ?? []).filter((a) => a.id !== actor.id));
      setConfirming(null);
      setEditing(null);
      setParams({}, { replace: true });
    });

  const machineNames = machines.map((m) => m.name);

  return (
    <main id="main" className="actors-board" tabIndex={-1}>
      <h1 className="sr-only">Actors</h1>
      <div className="actors-toolbar">
        {FILTERS.map(({ value, label }) => (
          <button
            key={value}
            type="button"
            className="pill"
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
        {role === "admin" && modelRoutes === "present" ? (
          <button
            type="button"
            className="btn btn--large actors-toolbar__models"
            aria-expanded={addingModels}
            onClick={() => setAddingModels((v) => !v)}
          >
            Add models
          </button>
        ) : null}
        <button type="button" className="btn btn--primary btn--large actors-toolbar__add" onClick={() => setEditing("new")}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add actor
        </button>
      </div>

      {errors.length > 0 ? (
        <p className="notice notice--error" role="alert">
          {errors.join(" · ")}
        </p>
      ) : null}

      {saveError ? <GuidedNotice error={saveError} /> : null}

      {addingModels && modelRoutes === "present" ? (
        <AddModelsPanel onClose={() => setAddingModels(false)} onApplied={reloadActors} />
      ) : null}

      {editing === "new" ? (
        <div className="actor-card actor-card--new">
          <ActorForm
            actor={null}
            machines={machineNames}
            busy={busy}
            onSave={(a) => save(a, true)}
            onCancel={() => setEditing(null)}
          />
        </div>
      ) : null}

      <div className="actors-roster">
        {shown.map((actor) => {
          const enabled = actor.enabled !== false;
          const open = actor.id === selected?.id;
          const slot = slotOf(actor);
          return (
            <fieldset
              key={actor.id}
              aria-label={actor.name}
              data-actor-id={actor.id}
              className={`actor-row plain-group${open ? " is-selected" : ""}${enabled ? "" : " is-disabled"}`}
              style={machineStyle(slot)}
            >
              <ActorHead
                actor={actor}
                slot={slot}
                enabled={enabled}
                open={open}
                pending={togglePending.has(actor.id)}
                onSelect={() => select(actor.id)}
                onToggle={(next) => void toggle(actor, next)}
              />

              {inUse?.actorId === actor.id ? (
                <InUseRefusal
                  actor={actor}
                  referrers={inUse.referrers}
                  busy={togglePending.has(actor.id)}
                  onForce={() => void toggle(actor, false, true)}
                  onKeep={() => setInUse(null)}
                />
              ) : null}

              {open ? (
                <div className="actor-row__details">
                  <ActorConfigLine actor={actor} onSource={(v) => void setSource(actor, v)} />

                  {editing === actor.id ? (
                    <ActorForm
                      actor={actor}
                      machines={machineNames}
                      busy={busy}
                      onSave={(a) => save(a, false)}
                      onCancel={() => setEditing(null)}
                    />
                  ) : (
                    <ActorCaps
                      actor={actor}
                      busy={busy}
                      confirming={confirming === actor.id}
                      onEdit={() => setEditing(actor.id)}
                      onAskDelete={() => setConfirming(actor.id)}
                      onCancelDelete={() => setConfirming(null)}
                      onDelete={() => void remove(actor)}
                    />
                  )}

                  {modelRoutes === "present" && references?.actorId === actor.id ? (
                    <UsedBy
                      actor={actor}
                      items={references.items}
                      error={references.error}
                      actors={all}
                      canMove={role === "editor" || role === "admin"}
                      onMoved={() => setRefsVersion((v) => v + 1)}
                    />
                  ) : null}
                </div>
              ) : null}
            </fieldset>
          );
        })}
        {actors && shown.length === 0 && errors.length === 0 ? (
          <p className="actors-empty">
            {kindOfFilter(filter) ? `No ${filter}s yet.` : "No actors yet."}
          </p>
        ) : null}
      </div>
    </main>
  );
}

export default ActorsBoard;
