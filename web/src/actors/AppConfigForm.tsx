import { useId } from "react";
import type { AppSurface } from "../api/actors";
import {
  ACTION_KINDS,
  APP_SURFACES,
  CONNECTION_FIELDS,
  isSecretKey,
  type AppDraft,
  type FormErrors,
  type HttpDraft,
} from "./app-config";
import { RemoveButton, Section, TextField } from "./fields";

export interface AppConfigFormProps {
  value: AppDraft;
  onChange: (next: AppDraft) => void;
  /** Field errors keyed by path (`surface`, `connection.<key>`, `events.<i>`, `probes.<i>.name`). */
  errors?: FormErrors;
}

const GRANT_HINT = "A reference to a stored secret, like grant:NAME. The secret itself is never typed here.";

/**
 * The app actor editor, disclosed in order: the surface first, then its
 * connection (secret-looking keys take `grant:NAME` references only), then
 * what the app declares (events, actions, probes, its own identity).
 */
export function AppConfigForm({ value, onChange, errors = {} }: Readonly<AppConfigFormProps>) {
  const uid = useId();
  const set = (patch: Partial<AppDraft>) => onChange({ ...value, ...patch });
  const surface = value.surface;

  return (
    <div className="actor-subform-stack">
      <div className="actor-form__field">
        <label htmlFor={`${uid}-surface`}>Surface</label>
        <select
          id={`${uid}-surface`}
          value={surface}
          aria-invalid={errors.surface ? true : undefined}
          aria-describedby={errors.surface ? `${uid}-surface-err` : undefined}
          onChange={(e) => set({ surface: e.target.value as AppSurface | "", connection: {} })}
        >
          <option value="">choose a surface</option>
          {APP_SURFACES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {errors.surface ? (
          <p id={`${uid}-surface-err`} className="actor-form__error">
            {errors.surface}
          </p>
        ) : null}
      </div>

      {surface ? (
        <>
          <Section title="Connection">
            <div className="actor-subform__grid">
              {CONNECTION_FIELDS[surface].map((field) => {
                const isSecret = isSecretKey(field.key);
                return (
                  <TextField
                    key={`${surface}-${field.key}`}
                    label={isSecret ? `${field.label} (grant reference)` : field.label}
                    value={value.connection[field.key] ?? ""}
                    onChange={(text) => set({ connection: { ...value.connection, [field.key]: text } })}
                    error={errors[`connection.${field.key}`]}
                    hint={isSecret ? GRANT_HINT : field.list ? "Separate with commas." : undefined}
                    placeholder={isSecret ? "grant:NAME" : undefined}
                    mono={isSecret}
                  />
                );
              })}
            </div>
          </Section>

          <Section title="Declarations">
            <fieldset className="actor-list plain-group" aria-label="Events">
              <span className="actor-list__title">Events it emits</span>
              {value.events.map((event, i) => (
                <div className="actor-list__row" key={`event-${i}`}>
                  <TextField
                    ariaLabel={`Event ${i + 1}`}
                    value={event}
                    placeholder="github.pr.opened"
                    error={errors[`events.${i}`]}
                    onChange={(text) => set({ events: value.events.map((e, j) => (j === i ? text : e)) })}
                  />
                  <RemoveButton label={`Remove event ${i + 1}`} onClick={() => set({ events: value.events.filter((_, j) => j !== i) })} />
                </div>
              ))}
              <button type="button" className="btn actor-add" onClick={() => set({ events: [...value.events, ""] })}>
                Add event
              </button>
            </fieldset>

            <fieldset className="actor-list plain-group" aria-label="Actions">
              <span className="actor-list__title">Actions it can perform</span>
              <div className="actor-checks">
                {ACTION_KINDS.map((kind) => {
                  const on = value.actions.includes(kind.name);
                  return (
                    <label key={kind.name} className="actor-check">
                      <input
                        type="checkbox"
                        checked={on}
                        onChange={() =>
                          set({ actions: on ? value.actions.filter((a) => a !== kind.name) : [...value.actions, kind.name] })
                        }
                      />
                      <span>
                        <strong className="mono">{kind.name}</strong> <span className="muted">{kind.summary}</span>
                      </span>
                    </label>
                  );
                })}
              </div>
            </fieldset>

            <fieldset className="actor-list plain-group" aria-label="Probes">
              <span className="actor-list__title">Probes</span>
              {value.probes.map((probe, i) => {
                const patch = (p: Partial<typeof probe>) =>
                  set({ probes: value.probes.map((x, j) => (j === i ? { ...x, ...p } : x)) });
                return (
                  <div className="actor-list__row actor-list__row--probe" key={`probe-${i}`}>
                    <TextField ariaLabel={`Probe ${i + 1} name`} placeholder="name" value={probe.name} onChange={(v) => patch({ name: v })} error={errors[`probes.${i}.name`]} />
                    <TextField ariaLabel={`Probe ${i + 1} command`} placeholder="command" value={probe.command} onChange={(v) => patch({ command: v })} error={errors[`probes.${i}.command`]} mono />
                    <TextField ariaLabel={`Probe ${i + 1} schedule`} placeholder="schedule (optional)" value={probe.schedule} onChange={(v) => patch({ schedule: v })} />
                    <RemoveButton label={`Remove probe ${i + 1}`} onClick={() => set({ probes: value.probes.filter((_, j) => j !== i) })} />
                  </div>
                );
              })}
              <button
                type="button"
                className="btn actor-add"
                onClick={() => set({ probes: [...value.probes, { name: "", command: "", schedule: "" }] })}
              >
                Add probe
              </button>
            </fieldset>

            <TextField
              label="Self identity"
              value={value.selfIdentity}
              onChange={(text) => set({ selfIdentity: text })}
              hint="The login of this app or bot, so its own events can be recognised."
            />
          </Section>
        </>
      ) : null}
    </div>
  );
}

export interface HttpPolicyFormProps {
  value: HttpDraft;
  onChange: (next: HttpDraft) => void;
  errors?: FormErrors;
}

/** `params.http`: the hosts an http.call may reach and the headers it sends (credentials as grant references). */
export function HttpPolicyForm({ value, onChange, errors = {} }: Readonly<HttpPolicyFormProps>) {
  return (
    <Section title="HTTP policy">
      <TextField
        label="Allowed hosts"
        value={value.allow}
        onChange={(allow) => onChange({ ...value, allow })}
        hint="Exact hostnames or IP addresses, separated by commas. Empty refuses every call."
      />
      <fieldset className="actor-list plain-group" aria-label="Headers">
        <span className="actor-list__title">Headers sent on every call</span>
        {value.headers.map((header, i) => (
          <div className="actor-list__row" key={`header-${i}`}>
            <TextField
              ariaLabel={`Header ${i + 1} name`}
              placeholder="Authorization"
              value={header.name}
              error={errors[`http.${i}.name`]}
              onChange={(name) => onChange({ ...value, headers: value.headers.map((h, j) => (j === i ? { ...h, name } : h)) })}
            />
            <TextField
              ariaLabel={`Header ${i + 1} value (grant reference)`}
              placeholder="grant:NAME"
              value={header.value}
              error={errors[`http.${i}.value`]}
              mono
              onChange={(v) => onChange({ ...value, headers: value.headers.map((h, j) => (j === i ? { ...h, value: v } : h)) })}
            />
            <RemoveButton label={`Remove header ${i + 1}`} onClick={() => onChange({ ...value, headers: value.headers.filter((_, j) => j !== i) })} />
          </div>
        ))}
        <button type="button" className="btn actor-add" onClick={() => onChange({ ...value, headers: [...value.headers, { name: "", value: "" }] })}>
          Add header
        </button>
      </fieldset>
    </Section>
  );
}

export default AppConfigForm;
