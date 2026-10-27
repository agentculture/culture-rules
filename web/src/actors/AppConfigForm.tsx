import { useId } from "react";
import type { AppSurface } from "../api/actors";
import {
  ACTION_KINDS,
  APP_SURFACES,
  CONNECTION_FIELDS,
  DISCORD_COMMAND_TYPES,
  DISCORD_OPTION_KINDS,
  connectionDefaults,
  emptyDiscordCommand,
  emptyDiscordOption,
  isSecretKey,
  type AppDraft,
  type ConnectionField,
  type DiscordCommandDraft,
  type DiscordOptionDraft,
  type FormErrors,
  type HttpDraft,
} from "./app-config";
import { RemoveButton, SelectField, Section, TextField } from "./fields";
import { useRowKeys } from "./useRowKeys";

export interface AppConfigFormProps {
  value: AppDraft;
  onChange: (next: AppDraft) => void;
  /**
   * Field errors keyed by path (`surface`, `connection.<key>`, `events.<i>`, `probes.<i>.name`,
   * `commands`, `commands.<i>.<field>`, `commands.<i>.options.<j>.<field>`).
   */
  errors?: FormErrors;
}

const GRANT_HINT = "A reference to a stored secret, like grant:NAME. The secret itself is never typed here.";

function connectionHint(isSecret: boolean, isList: boolean | undefined): string | undefined {
  if (isSecret) return GRANT_HINT;
  return isList ? "Separate with commas." : undefined;
}

interface ConnectionInputProps {
  field: ConnectionField;
  value: string;
  error?: string;
  onChange: (text: string) => void;
}

/** One connection field: an enum as a select (blank shows its default), anything else as text. */
function ConnectionInput({ field, value, error, onChange }: Readonly<ConnectionInputProps>) {
  if (field.options) {
    return (
      <SelectField label={field.label} value={value || field.options[0]} options={field.options} onChange={onChange} error={error} />
    );
  }
  const isSecret = isSecretKey(field.key);
  return (
    <TextField
      label={isSecret ? `${field.label} (grant reference)` : field.label}
      value={value}
      onChange={onChange}
      error={error}
      hint={connectionHint(isSecret, field.list || field.oneOrMany)}
      placeholder={isSecret ? "grant:NAME" : field.placeholder}
      mono={isSecret}
    />
  );
}

/** A checkbox in a row, named for its row, with its error announced. */
function RowCheck({ ariaLabel, text, checked, error, onChange }: Readonly<{
  ariaLabel: string;
  text: string;
  checked: boolean;
  error?: string;
  onChange: (checked: boolean) => void;
}>) {
  const uid = useId();
  return (
    <div className="actor-form__field">
      <label className="actor-check">
        <input
          type="checkbox"
          aria-label={ariaLabel}
          checked={checked}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${uid}-err` : undefined}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span>{text}</span>
      </label>
      {error ? (
        <p id={`${uid}-err`} className="actor-form__error">
          {error}
        </p>
      ) : null}
    </div>
  );
}

interface DiscordCommandsProps {
  value: DiscordCommandDraft[];
  onChange: (next: DiscordCommandDraft[]) => void;
  errors: FormErrors;
}

/**
 * A Discord app's application commands (`params.commands`, d12): each a slash command (a name,
 * a description and options) or a message command (a name only), with the private answer
 * given where no rule handles it. A rule picks one with its trigger's `command` filter.
 */
function DiscordCommands({ value, onChange, errors }: Readonly<DiscordCommandsProps>) {
  const rows = useRowKeys();
  const commandKeys = rows.keys("commands", value.length);
  const patch = (i: number, next: Partial<DiscordCommandDraft>) => onChange(value.map((c, j) => (j === i ? { ...c, ...next } : c)));
  const patchOption = (i: number, j: number, next: Partial<DiscordOptionDraft>) =>
    patch(i, { options: value[i].options.map((o, k) => (k === j ? { ...o, ...next } : o)) });
  return (
    <fieldset className="actor-list plain-group" aria-label="Commands">
      <span className="actor-list__title">Commands it registers</span>
      {errors.commands ? <p className="actor-form__error">{errors.commands}</p> : null}
      {value.map((command, i) => {
        const n = i + 1;
        const at = `commands.${i}`;
        const optionList = `${commandKeys[i]}:options`;
        const optionKeys = rows.keys(optionList, command.options.length);
        return (
          <fieldset key={commandKeys[i]} className="actor-command plain-group" aria-label={`Command ${n}`}>
            <div className="actor-list__row actor-list__row--command">
              <SelectField
                ariaLabel={`Command ${n} type`}
                value={command.type}
                options={DISCORD_COMMAND_TYPES}
                onChange={(type) => patch(i, { type: type as DiscordCommandDraft["type"] })}
                error={errors[`${at}.type`]}
              />
              <TextField
                ariaLabel={`Command ${n} name`}
                placeholder={command.type === "slash" ? "save" : "Save to library"}
                value={command.name}
                onChange={(name) => patch(i, { name })}
                error={errors[`${at}.name`]}
              />
              <RemoveButton
                label={`Remove command ${n}`}
                onClick={() => {
                  rows.drop("commands", i);
                  onChange(value.filter((_, j) => j !== i));
                }}
              />
            </div>
            {command.type === "slash" ? (
              <TextField
                ariaLabel={`Command ${n} description`}
                placeholder="description"
                value={command.description}
                onChange={(description) => patch(i, { description })}
                error={errors[`${at}.description`]}
              />
            ) : null}
            <TextField
              ariaLabel={`Command ${n} answer when not set up here`}
              placeholder="Not set up here (optional)"
              value={command.notHere}
              onChange={(notHere) => patch(i, { notHere })}
              error={errors[`${at}.not_here`]}
              hint="Answered privately where no rule handles this command."
            />
            {command.type === "slash" ? (
              <>
                {errors[`${at}.options`] ? <p className="actor-form__error">{errors[`${at}.options`]}</p> : null}
                {command.options.map((option, j) => {
                  const m = j + 1;
                  const name = `Command ${n} option ${m}`;
                  const oat = `${at}.options.${j}`;
                  return (
                    <div className="actor-list__row actor-list__row--option" key={optionKeys[j]}>
                      <TextField ariaLabel={`${name} name`} placeholder="name" value={option.name} onChange={(v) => patchOption(i, j, { name: v })} error={errors[`${oat}.name`]} mono />
                      <TextField ariaLabel={`${name} description`} placeholder="description" value={option.description} onChange={(v) => patchOption(i, j, { description: v })} error={errors[`${oat}.description`]} />
                      <SelectField
                        ariaLabel={`${name} kind`}
                        value={option.kind}
                        options={DISCORD_OPTION_KINDS}
                        onChange={(v) => patchOption(i, j, { kind: v as DiscordOptionDraft["kind"] })}
                        error={errors[`${oat}.kind`]}
                      />
                      <RowCheck ariaLabel={`${name} required`} text="Required" checked={option.required} onChange={(v) => patchOption(i, j, { required: v })} error={errors[`${oat}.required`]} />
                      <RemoveButton
                        label={`Remove command ${n} option ${m}`}
                        onClick={() => {
                          rows.drop(optionList, j);
                          patch(i, { options: command.options.filter((_, k) => k !== j) });
                        }}
                      />
                    </div>
                  );
                })}
                <button type="button" className="btn actor-add" aria-label={`Add option to command ${n}`} onClick={() => patch(i, { options: [...command.options, emptyDiscordOption()] })}>
                  Add option
                </button>
              </>
            ) : null}
          </fieldset>
        );
      })}
      <button type="button" className="btn actor-add" onClick={() => onChange([...value, emptyDiscordCommand()])}>
        Add command
      </button>
    </fieldset>
  );
}

/**
 * The app actor editor, disclosed in order: the surface first, then its
 * connection (secret-looking keys take `grant:NAME` references only), then
 * what the app declares (events, actions, probes, its own identity).
 */
export function AppConfigForm({ value, onChange, errors = {} }: Readonly<AppConfigFormProps>) {
  const uid = useId();
  const rows = useRowKeys();
  const eventKeys = rows.keys("events", value.events.length);
  const probeKeys = rows.keys("probes", value.probes.length);
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
          onChange={(e) => {
            const next = e.target.value as AppSurface | "";
            set({ surface: next, connection: connectionDefaults(next) });
          }}
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
              {CONNECTION_FIELDS[surface].map((field) => (
                <ConnectionInput
                  key={`${surface}-${field.key}`}
                  field={field}
                  value={value.connection[field.key] ?? ""}
                  onChange={(text) => set({ connection: { ...value.connection, [field.key]: text } })}
                  error={errors[`connection.${field.key}`]}
                />
              ))}
            </div>
          </Section>

          <Section title="Declarations">
            <fieldset className="actor-list plain-group" aria-label="Events">
              <span className="actor-list__title">Events it emits</span>
              {value.events.map((event, i) => (
                <div className="actor-list__row" key={eventKeys[i]}>
                  <TextField
                    ariaLabel={`Event ${i + 1}`}
                    value={event}
                    placeholder="github.pr.opened"
                    error={errors[`events.${i}`]}
                    onChange={(text) => set({ events: value.events.map((e, j) => (j === i ? text : e)) })}
                  />
                  <RemoveButton
                    label={`Remove event ${i + 1}`}
                    onClick={() => {
                      rows.drop("events", i);
                      set({ events: value.events.filter((_, j) => j !== i) });
                    }}
                  />
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
                      <strong className="mono">{kind.name}</strong>
                      <span className="muted">{kind.summary}</span>
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
                  <div className="actor-list__row actor-list__row--probe" key={probeKeys[i]}>
                    <TextField ariaLabel={`Probe ${i + 1} name`} placeholder="name" value={probe.name} onChange={(v) => patch({ name: v })} error={errors[`probes.${i}.name`]} />
                    <TextField ariaLabel={`Probe ${i + 1} command`} placeholder="command" value={probe.command} onChange={(v) => patch({ command: v })} error={errors[`probes.${i}.command`]} mono />
                    <TextField ariaLabel={`Probe ${i + 1} schedule`} placeholder="schedule (optional)" value={probe.schedule} onChange={(v) => patch({ schedule: v })} />
                    <RemoveButton
                      label={`Remove probe ${i + 1}`}
                      onClick={() => {
                        rows.drop("probes", i);
                        set({ probes: value.probes.filter((_, j) => j !== i) });
                      }}
                    />
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

            {surface === "discord" ? (
              <DiscordCommands value={value.commands} onChange={(commands) => set({ commands })} errors={errors} />
            ) : null}

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
  const rows = useRowKeys();
  const headerKeys = rows.keys("headers", value.headers.length);
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
          <div className="actor-list__row" key={headerKeys[i]}>
            <TextField
              ariaLabel={`Header ${i + 1} name`}
              placeholder="Authorization"
              value={header.name}
              error={errors[`http.${i}.name`]}
              onChange={(name) => onChange({ ...value, headers: value.headers.map((h, j) => (j === i ? { ...h, name } : h)) })}
            />
            <TextField
              ariaLabel={`Header ${i + 1} value (grant reference)`}
              placeholder="Bearer grant:NAME"
              value={header.value}
              error={errors[`http.${i}.value`]}
              mono
              onChange={(v) => onChange({ ...value, headers: value.headers.map((h, j) => (j === i ? { ...h, value: v } : h)) })}
            />
            <RemoveButton
              label={`Remove header ${i + 1}`}
              onClick={() => {
                rows.drop("headers", i);
                onChange({ ...value, headers: value.headers.filter((_, j) => j !== i) });
              }}
            />
          </div>
        ))}
        <button type="button" className="btn actor-add" onClick={() => onChange({ ...value, headers: [...value.headers, { name: "", value: "" }] })}>
          Add header
        </button>
      </fieldset>
      <TextField
        label="Save roots"
        value={value.saveRoots}
        error={errors["http.save_roots"]}
        mono
        onChange={(saveRoots) => onChange({ ...value, saveRoots })}
        hint="Absolute directories a call's save_to may name, separated by commas. Empty refuses save_to."
      />
      <TextField
        label="Largest saved body (bytes)"
        value={value.saveMaxBytes}
        error={errors["http.save_max_bytes"]}
        mono
        onChange={(saveMaxBytes) => onChange({ ...value, saveMaxBytes })}
        hint="Leave blank for 50 MiB (52428800)."
      />
    </Section>
  );
}

export default AppConfigForm;
