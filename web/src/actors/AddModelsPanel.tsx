import { useEffect, useId, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { enrolModels, getModelGateways, type ModelGateway } from "../api/actors";
import { ApiError } from "../api/client";
import type { EnrolModelsRequest, EnrolModelsResult } from "../api/types";

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

/** "qwen*, llama*" or one per line -> ["qwen*", "llama*"]. */
const splitList = (text: string) =>
  text
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);

const WRITES = new Set(["create", "update"]);

const VARIABLES = "/variables?name=model_gateways";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

const ErrorNotice = ({ text }: Readonly<{ text: string }>) => (
  <p className="notice notice--error" role="alert">
    {text}
  </p>
);

/** What shows while there is nothing to pick: reading, an unset variable, or an empty one. */
function GatewaysEmpty({
  gateways,
  loadError,
  onClose,
}: Readonly<{ gateways: ModelGateway[] | null | undefined; loadError: string | null; onClose: () => void }>) {
  const settled = gateways !== undefined || loadError !== null;
  if (!settled) return <p className="muted">Reading the model gateways…</p>;
  if (gateways?.length) return null;
  return (
    <>
      {loadError ? <ErrorNotice text={loadError} /> : null}
      {gateways === null && !loadError ? (
        <p className="models-panel__empty">
          No model gateways yet. Set <span className="mono">model_gateways</span> on the{" "}
          <Link to={VARIABLES}>Variables</Link> tab, one <span className="mono">machine=URL</span> per gateway.
        </p>
      ) : null}
      {gateways?.length === 0 ? (
        <p className="models-panel__empty">
          <span className="mono">model_gateways</span> lists no gateway. Add one on the{" "}
          <Link to={VARIABLES}>Variables</Link> tab.
        </p>
      ) : null}
      <div className="actor-form__actions">
        <button type="button" className="btn" onClick={onClose}>
          Close
        </button>
      </div>
    </>
  );
}

/** One labelled text input of the plan form. */
function TextField({
  id,
  label,
  value,
  placeholder,
  onChange,
}: Readonly<{ id: string; label: string; value: string; placeholder: string; onChange: (v: string) => void }>) {
  return (
    <div className="actor-form__field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        type="text"
        value={value}
        placeholder={placeholder}
        autoComplete="off"
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

/** The gateway checkboxes, the match and pool inputs, and Plan / Cancel. */
function PlanForm({
  gateways,
  chosen,
  match,
  pools,
  ready,
  busy,
  done,
  onToggle,
  onMatch,
  onPools,
  onSubmit,
  onClose,
}: Readonly<{
  gateways: ModelGateway[];
  chosen: string[];
  match: string;
  pools: string;
  ready: boolean;
  busy: boolean;
  done: boolean;
  onToggle: (machine: string, on: boolean) => void;
  onMatch: (v: string) => void;
  onPools: (v: string) => void;
  onSubmit: (event: FormEvent) => void;
  onClose: () => void;
}>) {
  const uid = useId();
  return (
    <form className="actor-form models-panel__form" onSubmit={onSubmit} aria-label="Plan models">
      <fieldset className="actor-form__wide plain-group models-panel__gateways">
        <legend className="actor-list__title">Gateways</legend>
        <div className="actor-checks">
          {gateways.map((g) => (
            <label key={g.machine} className="actor-check">
              <input
                type="checkbox"
                checked={chosen.includes(g.machine)}
                onChange={(e) => onToggle(g.machine, e.target.checked)}
              />
              <strong>{g.machine}</strong> <span className="mono muted">{g.url}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <TextField id={`${uid}-match`} label="Models to match" value={match} placeholder="qwen*, llama*" onChange={onMatch} />
      <TextField id={`${uid}-pools`} label="Pools" value={pools} placeholder="model=pool (optional)" onChange={onPools} />
      <div className="actor-form__actions">
        <button type="submit" className="btn btn--primary" disabled={!ready || busy}>
          Plan
        </button>
        <button type="button" className="btn" onClick={onClose}>
          {done ? "Done" : "Cancel"}
        </button>
      </div>
    </form>
  );
}

/** The server's dry-run plan, with the Apply button. */
function PlanView({
  plan,
  busy,
  onApply,
}: Readonly<{ plan: EnrolModelsResult; busy: boolean; onApply: () => void }>) {
  const writes = plan.changes.filter((c) => WRITES.has(c.action)).length;
  const also = Object.entries(plan.also_served_by ?? {}).filter(([, others]) => others.length);
  return (
    <div className="models-plan">
      <ul className="models-plan__list" aria-label="Plan">
        {plan.changes.map((c) => (
          <li key={c.id} className="models-plan__item" data-action={c.action}>
            <span className="models-plan__action">{c.action}</span>
            <strong className="models-plan__id">{c.id}</strong>
            {c.reason ? <span className="muted">{c.reason}</span> : null}
          </li>
        ))}
      </ul>
      {plan.changes.length === 0 ? <p className="muted">No model matches.</p> : null}
      {also.length ? (
        <p className="muted models-plan__note">
          {also.map(([model, others]) => `${model} is also on ${others.join(", ")}`).join(" · ")}
        </p>
      ) : null}
      {plan.unseen.length ? <p className="muted models-plan__note">No gateway lists {plan.unseen.join(", ")}.</p> : null}
      <div className="actor-form__actions">
        <button type="button" className="btn btn--primary" disabled={writes === 0 || busy} onClick={onApply}>
          {`Apply ${plural(writes, "change", "changes")}`}
        </button>
      </div>
    </div>
  );
}

/** What an apply wrote, and what it failed to write. */
function ResultView({ result }: Readonly<{ result: EnrolModelsResult }>) {
  const applied = result.applied ?? [];
  const failed = result.failed ?? [];
  return (
    <>
      <output className="models-plan__done">
        {applied.length
          ? applied.map((a) => `${a.action === "update" ? "Updated" : "Added"} ${a.id}`).join(" · ")
          : "Nothing was written."}
      </output>
      {failed.length ? <ErrorNotice text={failed.map((f) => `${f.id}: ${f.message}`).join(" · ")} /> : null}
    </>
  );
}

/**
 * Add models (t10): pick gateways from the `model_gateways` variable, say which models to
 * match (and any pool overrides), see the server's dry-run plan, then apply it. Each step
 * shows only when the one before it is filled in; a changed input drops the plan, so what
 * is applied is always what was shown.
 */
export function AddModelsPanel({
  onClose,
  onApplied,
}: Readonly<{ onClose: () => void; onApplied: () => void }>) {
  const [gateways, setGateways] = useState<ModelGateway[] | null | undefined>(undefined);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [match, setMatch] = useState("");
  const [pools, setPools] = useState("");
  const [plan, setPlan] = useState<EnrolModelsResult | null>(null);
  const [result, setResult] = useState<EnrolModelsResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getModelGateways(controller.signal).then(
      (g) => !controller.signal.aborted && setGateways(g),
      (err) => {
        if (controller.signal.aborted) return;
        setGateways(null);
        setLoadError(errorText(err));
      },
    );
    return () => controller.abort();
  }, []);

  /** Any edit invalidates the plan and the last answer. */
  const edited = () => {
    setPlan(null);
    setResult(null);
    setError(null);
  };

  /** Run `set` as an edit: the plan is dropped first. */
  const edit = (set: (v: string) => void) => (v: string) => {
    edited();
    set(v);
  };

  const toggle = (machine: string, on: boolean) => {
    edited();
    setChosen((c) => (on ? [...c, machine] : c.filter((m) => m !== machine)));
  };

  const request = (dryRun: boolean): EnrolModelsRequest => {
    const order = (gateways ?? []).map((g) => g.machine).filter((m) => chosen.includes(m));
    const poolList = splitList(pools);
    return { gateways: order, match: splitList(match), ...(poolList.length ? { pools: poolList } : {}), dry_run: dryRun };
  };

  const run = async (dryRun: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const answer = await enrolModels(request(dryRun));
      setPlan(dryRun ? answer : null);
      setResult(dryRun ? null : answer);
      if (!dryRun) onApplied();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(true);
  };

  const ready = chosen.length > 0 && splitList(match).length > 0;

  return (
    <section className="actor-card actor-card--new models-panel" aria-label="Add models">
      <GatewaysEmpty gateways={gateways} loadError={loadError} onClose={onClose} />
      {gateways?.length ? (
        <PlanForm
          gateways={gateways}
          chosen={chosen}
          match={match}
          pools={pools}
          ready={ready}
          busy={busy}
          done={result !== null}
          onToggle={toggle}
          onMatch={edit(setMatch)}
          onPools={edit(setPools)}
          onSubmit={submit}
          onClose={onClose}
        />
      ) : null}
      {error ? <ErrorNotice text={error} /> : null}
      {plan ? <PlanView plan={plan} busy={busy} onApply={() => void run(false)} /> : null}
      {result ? <ResultView result={result} /> : null}
    </section>
  );
}

export default AddModelsPanel;
