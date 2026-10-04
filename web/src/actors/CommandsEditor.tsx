import {
  PARAM_TYPES,
  emptyCommand,
  inlineEvalWarning,
  type CommandDraft,
  type FormErrors,
} from "./app-config";
import { RemoveButton, TextField } from "./fields";

export interface CommandsEditorProps {
  value: CommandDraft[];
  onChange: (next: CommandDraft[]) => void;
  /** Field errors keyed `<i>.name`, `<i>.argv`, `<i>.params.<j>`, `<i>.timeout`. */
  errors?: FormErrors;
}

/**
 * The runner's registered commands: a name, the argv template one token per
 * field (`{param}` is replaced by a declared parameter's value, never run
 * through a shell), typed parameters and a timeout.
 */
export function CommandsEditor({ value, onChange, errors = {} }: Readonly<CommandsEditorProps>) {
  const patch = (i: number, next: Partial<CommandDraft>) =>
    onChange(value.map((c, j) => (j === i ? { ...c, ...next } : c)));

  return (
    <div className="actor-subform-stack">
      {value.map((command, i) => {
        const n = i + 1;
        const warning = inlineEvalWarning(command.argv);
        return (
          <fieldset key={`command-${i}`} className="actor-command plain-group" aria-label={`Command ${n}`}>
            <div className="actor-list__row">
              <TextField ariaLabel={`Command ${n} name`} label="Name" value={command.name} error={errors[`${i}.name`]} onChange={(name) => patch(i, { name })} />
              <RemoveButton label={`Remove command ${n}`} onClick={() => onChange(value.filter((_, j) => j !== i))} />
            </div>

            <div className="actor-list">
              <span className="actor-list__title">Argv template, one token per field</span>
              {command.argv.map((token, k) => (
                <div className="actor-list__row" key={`token-${k}`}>
                  <TextField
                    ariaLabel={`Command ${n} token ${k + 1}`}
                    value={token}
                    mono
                    placeholder={k === 0 ? "program" : "argument or {param}"}
                    error={k === 0 ? errors[`${i}.argv`] : undefined}
                    onChange={(text) => patch(i, { argv: command.argv.map((t, j) => (j === k ? text : t)) })}
                  />
                  <RemoveButton label={`Remove token ${k + 1} of command ${n}`} onClick={() => patch(i, { argv: command.argv.filter((_, j) => j !== k) })} />
                </div>
              ))}
              <button type="button" className="btn actor-add" aria-label={`Add token to command ${n}`} onClick={() => patch(i, { argv: [...command.argv, ""] })}>
                Add token
              </button>
              {warning ? (
                <p role="note" className="actor-form__warn">
                  {warning}
                </p>
              ) : null}
            </div>

            <div className="actor-list">
              <span className="actor-list__title">Parameters</span>
              {command.params.map((param, j) => (
                <div className="actor-list__row actor-list__row--param" key={`param-${j}`}>
                  <TextField
                    ariaLabel={`Command ${n} parameter ${j + 1} name`}
                    placeholder="name"
                    value={param.name}
                    error={errors[`${i}.params.${j}`]}
                    onChange={(name) => patch(i, { params: command.params.map((p, k) => (k === j ? { ...p, name } : p)) })}
                  />
                  <select
                    aria-label={`Command ${n} parameter ${j + 1} type`}
                    value={param.type}
                    onChange={(e) => patch(i, { params: command.params.map((p, k) => (k === j ? { ...p, type: e.target.value } : p)) })}
                  >
                    {PARAM_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  <RemoveButton label={`Remove parameter ${j + 1} of command ${n}`} onClick={() => patch(i, { params: command.params.filter((_, k) => k !== j) })} />
                </div>
              ))}
              <button
                type="button"
                className="btn actor-add"
                aria-label={`Add parameter to command ${n}`}
                onClick={() => patch(i, { params: [...command.params, { name: "", type: "string" }] })}
              >
                Add parameter
              </button>
            </div>

            <TextField
              ariaLabel={`Command ${n} timeout`}
              label="Timeout (seconds)"
              value={command.timeout}
              error={errors[`${i}.timeout`]}
              hint="Blank uses the runner's default."
              onChange={(timeout) => patch(i, { timeout })}
            />
          </fieldset>
        );
      })}
      <button type="button" className="btn actor-add" onClick={() => onChange([...value, emptyCommand()])}>
        Add command
      </button>
    </div>
  );
}

export default CommandsEditor;
