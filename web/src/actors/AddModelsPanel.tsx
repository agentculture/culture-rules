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

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

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
  const uid = useId();
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
      if (dryRun) {
        setPlan(answer);
        setResult(null);
      } else {
        setPlan(null);
        setResult(answer);
        onApplied();
      }
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
  const writes = plan ? plan.changes.filter((c) => WRITES.has(c.action)).length : 0;
  const also = plan ? Object.entries(plan.also_served_by ?? {}).filter(([, others]) => others.length) : [];

  return (
    <section className="actor-card actor-card--new models-panel" aria-label="Add models">
      {gateways === undefined && !loadError ? <p className="muted">Reading the model gateways…</p> : null}
      {loadError ? (
        <p className="notice notice--error" role="alert">
          {loadError}
        </p>
      ) : null}
      {gateways === null && !loadError ? (
        <p className="models-panel__empty">
          No model gateways yet. Set <span className="mono">model_gateways</span> on the{" "}
          <Link to="/variables?name=model_gateways">Variables</Link> tab, one{" "}
          <span className="mono">machine=URL</span> per gateway.
        </p>
      ) : null}

      {gateways && gateways.length > 0 ? (
        <form className="actor-form models-panel__form" onSubmit={submit} aria-label="Plan models">
          <fieldset className="actor-form__wide plain-group models-panel__gateways">
            <legend className="actor-list__title">Gateways</legend>
            <div className="actor-checks">
              {gateways.map((g) => (
                <label key={g.machine} className="actor-check">
                  <input
                    type="checkbox"
                    checked={chosen.includes(g.machine)}
                    onChange={(e) => {
                      edited();
                      setChosen((c) => (e.target.checked ? [...c, g.machine] : c.filter((m) => m !== g.machine)));
                    }}
                  />
                  <strong>{g.machine}</strong> <span className="mono muted">{g.url}</span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="actor-form__field">
            <label htmlFor={`${uid}-match`}>Models to match</label>
            <input
              id={`${uid}-match`}
              type="text"
              value={match}
              placeholder="qwen*, llama*"
              autoComplete="off"
              onChange={(e) => {
                edited();
                setMatch(e.target.value);
              }}
            />
          </div>
          <div className="actor-form__field">
            <label htmlFor={`${uid}-pools`}>Pools</label>
            <input
              id={`${uid}-pools`}
              type="text"
              value={pools}
              placeholder="model=pool (optional)"
              autoComplete="off"
              onChange={(e) => {
                edited();
                setPools(e.target.value);
              }}
            />
          </div>
          <div className="actor-form__actions">
            <button type="submit" className="btn btn--primary" disabled={!ready || busy}>
              Plan
            </button>
            <button type="button" className="btn" onClick={onClose}>
              {result ? "Done" : "Cancel"}
            </button>
          </div>
        </form>
      ) : null}
      {gateways && gateways.length === 0 ? (
        <p className="models-panel__empty">
          <span className="mono">model_gateways</span> lists no gateway. Add one on the{" "}
          <Link to="/variables?name=model_gateways">Variables</Link> tab.
        </p>
      ) : null}
      {gateways === null || (gateways && gateways.length === 0) || loadError ? (
        <div className="actor-form__actions">
          <button type="button" className="btn" onClick={onClose}>
            Close
          </button>
        </div>
      ) : null}

      {error ? (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      ) : null}

      {plan ? (
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
          {plan.unseen.length ? (
            <p className="muted models-plan__note">No gateway lists {plan.unseen.join(", ")}.</p>
          ) : null}
          <div className="actor-form__actions">
            <button
              type="button"
              className="btn btn--primary"
              disabled={writes === 0 || busy}
              onClick={() => void run(false)}
            >
              {`Apply ${plural(writes, "change", "changes")}`}
            </button>
          </div>
        </div>
      ) : null}

      {result ? (
        <>
          <output className="models-plan__done">
            {(result.applied ?? []).length
              ? (result.applied ?? [])
                  .map((a) => `${a.action === "update" ? "Updated" : "Added"} ${a.id}`)
                  .join(" · ")
              : "Nothing was written."}
          </output>
          {(result.failed ?? []).length ? (
            <p className="notice notice--error" role="alert">
              {(result.failed ?? []).map((f) => `${f.id}: ${f.message}`).join(" · ")}
            </p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

export default AddModelsPanel;
