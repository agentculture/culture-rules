import { useId, useState, type FormEvent } from "react";
import type { Actor, ActorKind, ActorParams } from "../api/actors";
import { KINDS, parseCapabilities } from "./actors-view";
import { AppConfigForm, HttpPolicyForm } from "./AppConfigForm";
import { CommandsEditor } from "./CommandsEditor";
import {
  appDraftFrom,
  appParamsFrom,
  commandsFrom,
  commandsParamFrom,
  hasErrors,
  httpDraftFrom,
  httpParamsFrom,
  validateApp,
  validateCommands,
  validateHttp,
  type FormErrors,
} from "./app-config";

export interface ActorFormProps {
  /** The actor being edited, or null when adding a new one. */
  actor: Actor | null;
  machines: string[];
  busy: boolean;
  onSave: (actor: Actor) => void;
  onCancel: () => void;
}

const blank = (value: string) => (value.trim() === "" ? null : value.trim());

/** The inline edit / add form: every field of the actor the board shows, plus id and kind when adding. */
export function ActorForm({ actor, machines, busy, onSave, onCancel }: Readonly<ActorFormProps>) {
  const uid = useId();
  const adding = actor === null;
  const [id, setId] = useState(actor?.id ?? "");
  const [name, setName] = useState(actor?.name ?? "");
  const [kind, setKind] = useState<ActorKind>(actor?.kind ?? "agent");
  const [harness, setHarness] = useState(actor?.harness ?? "");
  const [model, setModel] = useState(actor?.model ?? "");
  const [machine, setMachine] = useState(actor?.machine ?? "");
  const [repo, setRepo] = useState(actor?.repo ?? "");
  const [caps, setCaps] = useState((actor?.capabilities ?? []).join(", "));

  const [appDraft, setAppDraft] = useState(() => appDraftFrom(actor?.params));
  const [commands, setCommands] = useState(() => commandsFrom(actor?.params));
  const [http, setHttp] = useState(() => httpDraftFrom(actor?.params?.http));
  const [submitted, setSubmitted] = useState(false);

  const effectiveKind = adding ? kind : actor.kind;
  const hasHttp = effectiveKind === "app" || effectiveKind === "service" || effectiveKind === "runner";
  const errors: FormErrors = {
    ...(effectiveKind === "app" ? validateApp(appDraft) : {}),
    ...(effectiveKind === "runner" ? validateCommands(commands) : {}),
    ...(hasHttp ? validateHttp(http) : {}),
  };
  const shown = submitted ? errors : {};

  /** The actor's params with this form's declarations applied; undefined when it has none to carry. */
  const buildParams = (): ActorParams | undefined => {
    let params: ActorParams = { ...(actor?.params ?? {}) };
    if (effectiveKind === "app") params = appParamsFrom(appDraft, params);
    if (effectiveKind === "runner") {
      if (commands.length || params.commands) params.commands = commandsParamFrom(commands);
    }
    if (hasHttp) {
      const policy = httpParamsFrom(http);
      if (policy) params.http = policy;
      else delete params.http;
    }
    return Object.keys(params).length ? params : undefined;
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setSubmitted(true);
    if (hasErrors(errors)) return;
    const params = buildParams();
    onSave({
      ...(actor ?? { config_source: "db" as const, enabled: true }),
      id: adding ? id.trim() : actor.id,
      name: name.trim(),
      kind: adding ? kind : actor.kind,
      harness: blank(harness),
      model: blank(model),
      machine: blank(machine),
      repo: blank(repo),
      capabilities: parseCapabilities(caps),
      ...(params ? { params } : {}),
    });
  };

  const field = (label: string, control: (fieldId: string) => JSX.Element) => (
    <div className="actor-form__field">
      <label htmlFor={`${uid}-${label}`}>{label}</label>
      {control(`${uid}-${label}`)}
    </div>
  );

  return (
    <form className="actor-form" onSubmit={submit} aria-label={adding ? "Add actor" : `Edit ${actor.name}`}>
      {adding
        ? field("Id", (f) => (
            <input id={f} type="text" value={id} onChange={(e) => setId(e.target.value)} required autoComplete="off" />
          ))
        : null}
      {field("Name", (f) => (
        <input id={f} type="text" value={name} onChange={(e) => setName(e.target.value)} required />
      ))}
      {adding
        ? field("Kind", (f) => (
            <select id={f} value={kind} onChange={(e) => setKind(e.target.value as ActorKind)}>
              {KINDS.map((k) => (
                <option key={k} value={k}>
                  {k}
                </option>
              ))}
            </select>
          ))
        : null}
      {field("Harness", (f) => (
        <input id={f} type="text" value={harness} onChange={(e) => setHarness(e.target.value)} />
      ))}
      {field("Model", (f) => (
        <input id={f} type="text" value={model} onChange={(e) => setModel(e.target.value)} />
      ))}
      {field("Machine", (f) => (
        <select id={f} value={machine} onChange={(e) => setMachine(e.target.value)}>
          <option value="">anywhere</option>
          {machines.map((m) => (
            <option key={m} value={m}>
              {m}
            </option>
          ))}
        </select>
      ))}
      {field("Repo", (f) => (
        <input id={f} type="text" value={repo} onChange={(e) => setRepo(e.target.value)} />
      ))}
      {field("Capabilities", (f) => (
        <input id={f} type="text" value={caps} onChange={(e) => setCaps(e.target.value)} placeholder="review, triage" />
      ))}
      {effectiveKind === "app" ? (
        <div className="actor-form__wide">
          <AppConfigForm value={appDraft} onChange={setAppDraft} errors={shown} />
        </div>
      ) : null}
      {effectiveKind === "runner" ? (
        <div className="actor-form__wide">
          <CommandsEditor value={commands} onChange={setCommands} errors={shown} />
        </div>
      ) : null}
      {hasHttp ? (
        <div className="actor-form__wide">
          <HttpPolicyForm value={http} onChange={setHttp} errors={shown} />
        </div>
      ) : null}
      <div className="actor-form__actions">
        <button type="submit" className="btn btn--primary" disabled={busy}>
          Save
        </button>
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
