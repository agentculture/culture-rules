import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useSearchParams } from "react-router-dom";
import {
  createActor,
  deleteActor,
  listActors,
  setActorEnabled,
  updateActor,
  type Actor,
} from "../api/actors";
import { ApiError, listMachines } from "../api/client";
import { settleAll } from "../api/settle";
import type { Machine } from "../api/types";
import { setAgentState } from "../agent-state/store";
import { machineColors } from "../culture-design/chart";
import { MachineDot, Switch, machineStyle } from "../culture-design/stages";
import { usePending } from "../usePending";
import { useTabReady } from "../routes/useTabReady";
import { ActorForm } from "./ActorForm";
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
  const [filter, setFilter] = useState<KindFilter>("all");
  /** The actor id being edited, the literal "new" for the create form, or null. */
  const [editing, setEditing] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
    setParams({ id }, { replace: true });
  };

  /** Run a mutation: one at a time, its failure named in the alert, never swallowed. */
  const act = useCallback(async (work: () => Promise<void>) => {
    setBusy(true);
    setActionError(null);
    try {
      await work();
    } catch (err) {
      setActionError(errorText(err));
    } finally {
      setBusy(false);
    }
  }, []);

  const replace = (next: Actor) =>
    setActors((current) => (current ?? []).map((a) => (a.id === next.id ? next : a)));

  const { pending: togglePending, run: runToggle } = usePending();
  const toggle = (actor: Actor, enabled: boolean) =>
    runToggle(actor.id, () =>
      act(async () => {
        await setActorEnabled(actor.id, enabled);
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
    });

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
              <div className="actor-row__head">
                <button
                  type="button"
                  className="actor-row__name"
                  aria-expanded={open}
                  onClick={() => select(actor.id)}
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
                  disabled={togglePending.has(actor.id)}
                  onChange={(next) => void toggle(actor, next)}
                />
              </div>

              {open ? (
                <div className="actor-row__details">
                  <div className="actor-row__config">
                    <SourceSwitch
                      value={actor.config_source === "repo" ? "repo" : "db"}
                      onChange={(v) => void setSource(actor, v)}
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

                  {editing === actor.id ? (
                    <ActorForm
                      actor={actor}
                      machines={machineNames}
                      busy={busy}
                      onSave={(a) => save(a, false)}
                      onCancel={() => setEditing(null)}
                    />
                  ) : (
                    <div className="actor-row__caps">
                      <ul className="caps" aria-label="Capabilities">
                        {(actor.capabilities ?? []).map((cap) => (
                          <li key={cap} className="cap">
                            {cap}
                          </li>
                        ))}
                      </ul>
                      {confirming === actor.id ? (
                        <fieldset className="actor-row__actions plain-group" aria-label={`Delete ${actor.name}?`}>
                          <span className="confirm-text">Delete {actor.name}?</span>
                          <button type="button" className="btn btn--danger" disabled={busy} onClick={() => void remove(actor)}>
                            Confirm delete
                          </button>
                          <button type="button" className="btn" onClick={() => setConfirming(null)}>
                            Cancel
                          </button>
                        </fieldset>
                      ) : (
                        <span className="actor-row__actions">
                          <button
                            type="button"
                            className="icon-button"
                            aria-label={`Edit ${actor.name}`}
                            onClick={() => setEditing(actor.id)}
                          >
                            <PencilIcon />
                          </button>
                          <button
                            type="button"
                            className="icon-button icon-button--danger"
                            aria-label={`Delete ${actor.name}`}
                            onClick={() => setConfirming(actor.id)}
                          >
                            <TrashIcon />
                          </button>
                        </span>
                      )}
                    </div>
                  )}
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
