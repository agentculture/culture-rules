import { useState } from "react";
import type { Machine, Placement } from "../api/types";
import type { Actor, Step } from "../api/workflows";
import { placementMode, stepLabel, type PlacementMode } from "./model";
import Panel from "./Panel";

const MODES: { mode: PlacementMode; label: string }[] = [
  { mode: "machine", label: "On a machine" },
  { mode: "actor", label: "Via an actor" },
  { mode: "requirement", label: "Needs capabilities" },
  { mode: "default", label: "Engine default" },
];

/** Where one step runs: a machine, an actor (runs where it lives), or a capability requirement. */
export function PlacementEditor({
  step,
  machines,
  actors,
  returnFocus,
  onApply,
  onClose,
}: {
  step: Step;
  machines: readonly Machine[];
  actors: readonly Actor[];
  returnFocus?: HTMLElement | null;
  onApply: (placement: Placement | null) => void;
  onClose: () => void;
}) {
  const p = step.placement;
  const [mode, setMode] = useState<PlacementMode>(placementMode(p));
  const [machine, setMachine] = useState(p?.machine ?? machines[0]?.name ?? "");
  const [actor, setActor] = useState(p?.actor ?? actors[0]?.id ?? "");
  const [requirement, setRequirement] = useState((p?.requirement ?? []).join(", "));
  const label = stepLabel(step);

  const placement = (): Placement | null => {
    if (mode === "machine" && machine) return { machine };
    if (mode === "actor" && actor) return { actor };
    const caps = requirement
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean);
    if (mode === "requirement" && caps.length) return { requirement: caps };
    return null;
  };

  return (
    <Panel label={`Placement of ${label}`} onClose={onClose} returnFocus={returnFocus}>
      <form
        className="wf-form"
        onSubmit={(e) => {
          e.preventDefault();
          onApply(placement());
        }}
      >
        <h2 className="wf-panel__title">Where {label} runs</h2>
        <fieldset className="wf-modes">
          <legend className="sr-only">Placement</legend>
          {MODES.map((m) => (
            <label key={m.mode} className="wf-mode">
              <input
                type="radio"
                name="placement-mode"
                value={m.mode}
                checked={mode === m.mode}
                onChange={() => setMode(m.mode)}
              />
              {m.label}
            </label>
          ))}
        </fieldset>
        {mode === "machine" ? (
          <label className="wf-field">
            Machine
            <select value={machine} onChange={(e) => setMachine(e.target.value)}>
              {machines.map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {mode === "actor" ? (
          <label className="wf-field">
            Actor
            <select value={actor} onChange={(e) => setActor(e.target.value)}>
              {actors.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.id}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        {mode === "requirement" ? (
          <label className="wf-field">
            Capabilities
            <input
              type="text"
              value={requirement}
              placeholder="gpu, cuda"
              onChange={(e) => setRequirement(e.target.value)}
            />
          </label>
        ) : null}
        <div className="wf-form__actions">
          <button type="button" className="wf-button" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" className="wf-button wf-button--primary">
            Apply
          </button>
        </div>
      </form>
    </Panel>
  );
}

export default PlacementEditor;
