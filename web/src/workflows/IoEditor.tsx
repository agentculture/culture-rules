/**
 * The editor behind the canvas's `in` and `out` nodes (selected by click,
 * or Enter / Space while focused):
 *
 *   in   — the workflow's description and its inputs: name, type,
 *          required, description; add and remove.
 *   out  — its outputs (name, type, where each comes from: an input, a
 *          variable or a step's output port; the textual reference stays
 *          inspectable and editable under "Sources as text", never
 *          required) and its variables (name, type, a JSON default).
 *
 * Every valid change applies to the draft at once, as in the step editor.
 * A refused value (an empty, ambiguous or taken name; malformed JSON; a
 * reference to nothing) stays in its field with guided text and never
 * reaches the draft, and Done waits until it is fixed. Renames follow the
 * references that read the old name (see io.ts).
 */
import { useRef, useState, type ReactNode } from "react";
import GuidedNotice from "../components/GuidedNotice";
import { PORT_TYPES, type Port, type PortType, type WorkflowDef, type WorkflowOutput } from "../api/workflows";
import {
  addInput,
  addOutput,
  addVariable,
  ioNameProblem,
  outputSourceOptions,
  outputSourceProblem,
  patchInput,
  patchOutput,
  patchVariable,
  removeInput,
  removeOutput,
  removeVariable,
  renameInput,
  renameOutput,
  renameVariable,
  type Variable,
} from "./io";
import Panel from "./Panel";

type Code = string | null;
type Report = (code: Code) => void;

/**
 * A text field whose committed value lives in the draft: what is typed stays
 * local until it is valid; a change from outside (a picker, a remove) resets it.
 */
function useDraftText(committed: string, normalise: (text: string) => string = (t) => t) {
  const [text, setText] = useState(committed);
  const [code, setCode] = useState<Code>(null);
  const seen = useRef(committed);
  if (seen.current !== committed) {
    // Adjusting state to a new prop during render (React's documented pattern).
    seen.current = committed;
    if (normalise(text) !== committed) {
      setText(committed);
      setCode(null);
    }
  }
  /** Take typed text; `check` answers a guidance code, or null to commit it. */
  const take = (next: string, check: (t: string) => Code, commit: (t: string) => void, report: Report) => {
    setText(next);
    const problem = check(next);
    setCode(problem);
    report(problem);
    if (problem === null) commit(next);
  };
  return { text, code, take };
}

const NAME_HINT: Record<string, string> = {
  empty: "needs a name.",
  unsafe_id: "can use letters, digits, - and _ (no dots or spaces).",
  duplicate: "needs a name no other one here has.",
};

function TypeSelect({ label, value, onChange }: Readonly<{ label: string; value: PortType | undefined; onChange: (t: PortType) => void }>) {
  return (
    <select aria-label={label} value={value ?? "any"} onChange={(e) => onChange(e.target.value as PortType)}>
      {PORT_TYPES.map((t) => (
        <option key={t} value={t}>
          {t}
        </option>
      ))}
    </select>
  );
}

/** One row of an io list, with any refusal shown under it. */
function ItemRow({ children, notice }: Readonly<{ children: ReactNode; notice: ReactNode }>) {
  return (
    <div className="wf-io-item">
      <div className="wf-ports__row">{children}</div>
      {notice}
    </div>
  );
}

function Refusal({ code, hint }: Readonly<{ code: Code; hint: string }>) {
  if (!code) return null;
  return (
    <div className="wf-field__error">
      <GuidedNotice code={code} />
      <p className="wf-field__hint">{hint}</p>
    </div>
  );
}

function useNameInput({
  word,
  name,
  others,
  onRename,
  report,
}: Readonly<{ word: string; name: string; others: string[]; onRename: (to: string) => void; report: Report }>) {
  const field = useDraftText(name);
  return {
    input: (
      <input
        type="text"
        className="wf-ports__name"
        aria-label={`Name of ${word} ${name}`}
        aria-invalid={field.code ? true : undefined}
        value={field.text}
        onChange={(e) => field.take(e.target.value, (t) => (t === name ? null : ioNameProblem(others, t)), onRename, report)}
      />
    ),
    notice: <Refusal code={field.code} hint={`This ${word} ${NAME_HINT[field.code ?? ""] ?? ""}`} />,
  };
}

function InputRow({
  port,
  others,
  workflow,
  onChange,
  report,
}: Readonly<{ port: Port; others: string[]; workflow: WorkflowDef; onChange: (wf: WorkflowDef) => void; report: Report }>) {
  const name = useNameInput({
    word: "input",
    name: port.name,
    others,
    onRename: (to) => onChange(renameInput(workflow, port.name, to)),
    report,
  });
  const patch = (p: Partial<Omit<Port, "name">>) => onChange(patchInput(workflow, port.name, p));
  return (
    <ItemRow notice={name.notice}>
      {name.input}
      <TypeSelect label={`Type of input ${port.name}`} value={port.type} onChange={(type) => patch({ type })} />
      <label className="wf-ports__required">
        <input
          type="checkbox"
          aria-label={`Input ${port.name} is required`}
          checked={port.required !== false}
          onChange={(e) => patch({ required: e.target.checked })}
        />
        <span aria-hidden="true">Required</span>
      </label>
      <input
        type="text"
        aria-label={`Description of input ${port.name}`}
        placeholder="Description"
        value={port.description ?? ""}
        onChange={(e) => patch({ description: e.target.value })}
      />
      <RemoveButton label={`Remove input ${port.name}`} onClick={() => onChange(removeInput(workflow, port.name))} />
    </ItemRow>
  );
}

function OutputRow({
  output,
  others,
  workflow,
  onChange,
  report,
}: Readonly<{
  output: WorkflowOutput;
  others: string[];
  workflow: WorkflowDef;
  onChange: (wf: WorkflowDef) => void;
  report: Report;
}>) {
  const name = useNameInput({
    word: "output",
    name: output.name,
    others,
    onRename: (to) => onChange(renameOutput(workflow, output.name, to)),
    report,
  });
  const options = outputSourceOptions(workflow, output.name);
  const current = output.source ?? "";
  const known = current === "" || options.some((o) => o.value === current);
  return (
    <ItemRow notice={name.notice}>
      {name.input}
      <TypeSelect
        label={`Type of output ${output.name}`}
        value={output.type}
        onChange={(type) => onChange(patchOutput(workflow, output.name, { type }))}
      />
      <select
        aria-label={`${output.name} comes from`}
        className="wf-ports__wire"
        value={current}
        onChange={(e) => onChange(patchOutput(workflow, output.name, { source: e.target.value || null }))}
      >
        <option value="">Not set</option>
        {known ? null : <option value={current}>{current}</option>}
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <RemoveButton label={`Remove output ${output.name}`} onClick={() => onChange(removeOutput(workflow, output.name))} />
    </ItemRow>
  );
}

/** The advanced, textual form of one output's source. */
function SourceText({
  output,
  workflow,
  onChange,
  report,
}: Readonly<{ output: WorkflowOutput; workflow: WorkflowDef; onChange: (wf: WorkflowDef) => void; report: Report }>) {
  const field = useDraftText(output.source ?? "", (t) => t.trim());
  return (
    <div className="wf-field">
      <label className="wf-field">
        <span>{output.name}</span>
        <input
          type="text"
          className="wf-io-source"
          aria-label={`Source of output ${output.name}`}
          aria-invalid={field.code ? true : undefined}
          spellCheck={false}
          placeholder="inputs.<name>, vars.<name> or steps.<id>.outputs.<port>"
          value={field.text}
          onChange={(e) =>
            field.take(
              e.target.value,
              (t) => outputSourceProblem(workflow, output.name, t.trim()),
              (t) => onChange(patchOutput(workflow, output.name, { source: t.trim() || null })),
              report,
            )
          }
        />
      </label>
      <Refusal
        code={field.code}
        hint="Use inputs.<name>, vars.<name> or steps.<id>.outputs.<port>, naming one of a matching type. Leave it empty to unset it."
      />
    </div>
  );
}

const jsonText = (v: unknown) => (v === undefined ? "" : JSON.stringify(v));

/** A default as typed: empty is none, anything else must be JSON. */
function parseDefault(t: string): { ok: true; value: unknown } | { ok: false } {
  if (t.trim() === "") return { ok: true, value: undefined };
  try {
    return { ok: true, value: JSON.parse(t) as unknown };
  } catch {
    return { ok: false };
  }
}

function VariableRow({
  variable,
  others,
  workflow,
  onChange,
  report,
}: Readonly<{
  variable: Variable;
  others: string[];
  workflow: WorkflowDef;
  onChange: (wf: WorkflowDef) => void;
  report: (field: string) => Report;
}>) {
  const name = useNameInput({
    word: "variable",
    name: variable.name,
    others,
    onRename: (to) => onChange(renameVariable(workflow, variable.name, to)),
    report: report("name"),
  });
  const def = useDraftText(jsonText(variable.default), (t) => {
    const r = parseDefault(t);
    return r.ok ? jsonText(r.value) : t;
  });
  const parse = parseDefault;
  return (
    <ItemRow
      notice={
        <>
          {name.notice}
          <Refusal
            code={def.code}
            hint={`The default of ${variable.name} needs to be JSON, such as 0, "text", true, [1, 2] or {"a": 1}. Leave it empty for none.`}
          />
        </>
      }
    >
      {name.input}
      <TypeSelect
        label={`Type of variable ${variable.name}`}
        value={variable.type}
        onChange={(type) => onChange(patchVariable(workflow, variable.name, { type }))}
      />
      <input
        type="text"
        className="wf-io-default"
        aria-label={`Default of variable ${variable.name}`}
        aria-invalid={def.code ? true : undefined}
        spellCheck={false}
        placeholder="Default (JSON)"
        value={def.text}
        onChange={(e) =>
          def.take(
            e.target.value,
            (t) => (parse(t).ok ? null : "malformed"),
            (t) => {
              const r = parse(t);
              if (r.ok) onChange(patchVariable(workflow, variable.name, { default: r.value }));
            },
            report("default"),
          )
        }
      />
      <RemoveButton label={`Remove variable ${variable.name}`} onClick={() => onChange(removeVariable(workflow, variable.name))} />
    </ItemRow>
  );
}

function RemoveButton({ label, onClick }: Readonly<{ label: string; onClick: () => void }>) {
  return (
    <button type="button" className="wf-ports__remove" aria-label={label} onClick={onClick}>
      ×
    </button>
  );
}

function Section({
  legend,
  empty,
  add,
  onAdd,
  children,
}: Readonly<{ legend: string; empty: boolean; add: string; onAdd: () => void; children: ReactNode }>) {
  return (
    <fieldset className="wf-ports wf-io-section">
      <legend className="wf-ports__legend">{legend}</legend>
      {empty ? <p className="wf-ports__empty">None yet.</p> : null}
      {children}
      <button type="button" className="wf-button" onClick={onAdd}>
        {add}
      </button>
    </fieldset>
  );
}

const without = <T,>(list: readonly T[], i: number) => list.filter((_, j) => j !== i);

export function IoEditor({
  workflow,
  side,
  returnFocus,
  onChange,
  onClose,
}: Readonly<{
  workflow: WorkflowDef;
  side: "in" | "out";
  returnFocus?: HTMLElement | null;
  onChange: (wf: WorkflowDef) => void;
  onClose: () => void;
}>) {
  const [errors, setErrors] = useState<Record<string, Code>>({});
  // Removing a row remounts its list, so no row keeps another's half-typed text.
  const [generation, setGeneration] = useState(0);
  const report = (key: string): Report => (code) =>
    setErrors((e) => (e[key] === code ? e : { ...e, [key]: code }));
  const blocked = Object.values(errors).some(Boolean);
  const change = (wf: WorkflowDef) => {
    const removed =
      (wf.inputs ?? []).length < (workflow.inputs ?? []).length ||
      (wf.outputs ?? []).length < (workflow.outputs ?? []).length ||
      (wf.variables ?? []).length < (workflow.variables ?? []).length;
    if (removed) {
      setErrors({});
      setGeneration((g) => g + 1);
    }
    onChange(wf);
  };

  const inputs = workflow.inputs ?? [];
  const outputs = workflow.outputs ?? [];
  const variables = workflow.variables ?? [];
  const names = (list: readonly { name: string }[], i: number) => without(list, i).map((x) => x.name);

  const body =
    side === "in" ? (
      <>
        <h2 className="wf-panel__title">Inputs</h2>
        <label className="wf-field">
          <span>Workflow description</span>
          <textarea
            rows={2}
            value={workflow.description ?? ""}
            onChange={(e) => onChange({ ...workflow, description: e.target.value })}
          />
        </label>
        <Section legend="Inputs" empty={inputs.length === 0} add="Add input" onAdd={() => onChange(addInput(workflow).workflow)}>
          {inputs.map((p, i) => (
            <InputRow
              key={`${generation}-${i}`}
              port={p}
              others={names(inputs, i)}
              workflow={workflow}
              onChange={change}
              report={report(`in-${generation}-${i}`)}
            />
          ))}
        </Section>
      </>
    ) : (
      <>
        <h2 className="wf-panel__title">Outputs and variables</h2>
        <Section legend="Outputs" empty={outputs.length === 0} add="Add output" onAdd={() => onChange(addOutput(workflow).workflow)}>
          {outputs.map((o, i) => (
            <OutputRow
              key={`${generation}-${i}`}
              output={o}
              others={names(outputs, i)}
              workflow={workflow}
              onChange={change}
              report={report(`out-${generation}-${i}`)}
            />
          ))}
        </Section>
        {outputs.length > 0 ? (
          <details className="wf-advanced">
            <summary className="wf-advanced__summary">Sources as text</summary>
            <div className="wf-advanced__body">
              {outputs.map((o, i) => (
                <SourceText
                  key={`${generation}-${i}`}
                  output={o}
                  workflow={workflow}
                  onChange={change}
                  report={report(`src-${generation}-${i}`)}
                />
              ))}
            </div>
          </details>
        ) : null}
        <Section
          legend="Variables"
          empty={variables.length === 0}
          add="Add variable"
          onAdd={() => onChange(addVariable(workflow).workflow)}
        >
          {variables.map((v, i) => (
            <VariableRow
              key={`${generation}-${i}`}
              variable={v}
              others={names(variables, i)}
              workflow={workflow}
              onChange={change}
              report={(field) => report(`var-${field}-${generation}-${i}`)}
            />
          ))}
        </Section>
      </>
    );

  return (
    <Panel
      label={side === "in" ? "Edit inputs" : "Edit outputs"}
      onClose={onClose}
      returnFocus={returnFocus}
      className="wf-panel--wide wf-panel--io"
    >
      <div className="wf-form">
        {body}
        <div className="wf-form__actions">
          <button type="button" className="wf-button wf-button--primary" disabled={blocked} onClick={onClose}>
            Done
          </button>
        </div>
      </div>
    </Panel>
  );
}

export default IoEditor;
