import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import {
  getVariableHistory,
  getVariableRefs,
  listVariables,
  putVariable,
  type Scalar,
  type Variable,
  type VariableRef,
  type VariableValue,
  type VariableVersion,
} from "../api/variables";
import GuidedNotice from "../components/GuidedNotice";
import { useWhoami } from "../hooks/useWhoami";
import { useTabReady } from "./useTabReady";
import "./variables.css";

const describe = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

const NUMBER = /^-?\d+(\.\d+)?$/;

/** One item as text: a scalar's own text, nothing for null. */
const itemText = (v: Scalar) => (v === null ? "" : String(v));

/** The value as it reads in a list row or a history line. */
export function valueText(value: VariableValue): string {
  return Array.isArray(value) ? value.map(itemText).join(", ") : itemText(value);
}

/** Text typed for one item, typed like the existing items (numbers stay numbers). */
function typedLike(sample: Scalar | undefined, text: string): Scalar {
  if (typeof sample === "number" && NUMBER.test(text)) return Number(text);
  if (typeof sample === "boolean" && (text === "true" || text === "false")) return text === "true";
  return text;
}

/** The value a draft would save: a list one item per line, a scalar its text. */
export function parseDraft(current: VariableValue, text: string): VariableValue {
  if (Array.isArray(current)) {
    const sample = current.find((v) => v !== null);
    const uniform = current.every((v) => typeof v === typeof sample);
    return text
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
      .map((line) => typedLike(uniform ? sample : undefined, line));
  }
  return typedLike(current ?? undefined, text.trim());
}

const draftOf = (value: VariableValue) =>
  Array.isArray(value) ? value.map(itemText).join("\n") : itemText(value);

interface DetailProps {
  variable: Variable;
  admin: boolean;
  history: VariableVersion[];
  refs: VariableRef[];
  detailError: string | null;
  onSaved: (saved: Variable) => void;
}

function ValueEditor({ variable, admin, onSaved }: Readonly<Pick<DetailProps, "variable" | "admin" | "onSaved">>) {
  const isList = Array.isArray(variable.value);
  const [text, setText] = useState(draftOf(variable.value));
  const [pending, setPending] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);
  useEffect(() => {
    setText(draftOf(variable.value));
    setRefused(null);
  }, [variable.name, variable.version, variable.value]);

  if (!admin) {
    return (
      <>
        {Array.isArray(variable.value) ? (
          <ul className="vars-items" aria-label="Current items">
            {variable.value.map((item, i) => (
              <li key={`${itemText(item)}-${i}`}>{itemText(item)}</li>
            ))}
          </ul>
        ) : (
          <p className="vars-scalar">{valueText(variable.value)}</p>
        )}
        <p className="vars-note">Only admins can change variables.</p>
      </>
    );
  }

  const next = parseDraft(variable.value, text);
  const unchanged = JSON.stringify(next) === JSON.stringify(variable.value);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (unchanged || pending) return;
    setPending(true);
    setRefused(null);
    try {
      onSaved(await putVariable(variable.name, next));
    } catch (err) {
      setRefused(err instanceof ApiError ? err : new ApiError(0, "unreachable", describe(err)));
    } finally {
      setPending(false);
    }
  };
  const label = isList ? "Items, one per line" : "Value";
  return (
    <form className="vars-form" onSubmit={submit} aria-label={`Edit ${variable.name}`}>
      <label>
        <span>{label}</span>
        {isList ? (
          <textarea rows={Math.max(3, text.split("\n").length + 1)} value={text} onChange={(e) => setText(e.target.value)} />
        ) : (
          <input value={text} onChange={(e) => setText(e.target.value)} />
        )}
      </label>
      {refused ? <GuidedNotice error={refused} /> : null}
      <div className="vars-actions">
        <button type="submit" className="vars-btn vars-btn--primary" disabled={unchanged || pending}>
          Save new version
        </button>
        <button type="button" className="vars-btn" disabled={unchanged || pending} onClick={() => setText(draftOf(variable.value))}>
          Revert
        </button>
      </div>
    </form>
  );
}

function Detail({ variable, admin, history, refs, detailError, onSaved }: Readonly<DetailProps>) {
  const newestFirst = [...history].reverse();
  return (
    <section className="vars-detail" aria-labelledby="vars-detail-name">
      <h2 id="vars-detail-name" className="vars-detail__name">
        {variable.name}
      </h2>
      <p className="vars-detail__meta">
        {variable.description ? `${variable.description}. ` : ""}Version {variable.version}, by {variable.updated_by}
      </p>
      <p className="vars-detail__ref">
        Used in a rule as <code>vars.{variable.name}</code>
      </p>
      <ValueEditor variable={variable} admin={admin} onSaved={onSaved} />
      {detailError ? (
        <p className="notice notice--error" role="alert">
          {detailError}
        </p>
      ) : null}
      <h3 className="vars-detail__heading">Version history</h3>
      <ol className="vars-history" aria-label="Version history">
        {newestFirst.map((v) => (
          <li key={v.version} className="vars-history__row">
            <strong>v{v.version}</strong>
            <span className="vars-history__value">{valueText(v.value)}</span>
            <span className="vars-history__by">
              {v.updated_by}, {v.updated_at.slice(0, 10)}
            </span>
          </li>
        ))}
      </ol>
      <h3 className="vars-detail__heading">Rules that use it</h3>
      {refs.length === 0 ? (
        <p className="vars-note">No rule uses this variable yet.</p>
      ) : (
        <ul className="vars-refs" aria-label="Used by rules">
          {refs.map((r) => (
            <li key={r.id}>
              <Link to={`/rules/${encodeURIComponent(r.id)}`} className="vars-refs__link">
                {r.name}
                {r.enabled ? "" : " (off)"}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The Variables tab: the shared variables rules read as `vars.<name>`. The
 * selected one (`?name=`, else the first) shows its value, an admin-only
 * editor that appends a version, the version history and the rules that
 * reference it.
 */
export function Variables() {
  const whoami = useWhoami();
  const admin = whoami.status === "signed-in" && whoami.role === "admin";
  const [params] = useSearchParams();
  const [variables, setVariables] = useState<Variable[] | null>(null);
  const [loadErrors, setLoadErrors] = useState<string[]>([]);
  const [history, setHistory] = useState<VariableVersion[]>([]);
  const [refs, setRefs] = useState<VariableRef[]>([]);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listVariables(controller.signal)
      .then(setVariables)
      .catch((err) => {
        if (controller.signal.aborted) return;
        setLoadErrors([describe(err)]);
        setVariables([]);
      });
    return () => controller.abort();
  }, []);

  const all = variables ?? [];
  const requested = params.get("name");
  const selected = all.find((v) => v.name === requested) ?? all[0] ?? null;
  const name = selected?.name ?? null;
  const version = selected?.version;

  useEffect(() => {
    if (!name) return;
    const controller = new AbortController();
    setDetailError(null);
    Promise.allSettled([getVariableHistory(name, controller.signal), getVariableRefs(name, controller.signal)]).then(
      ([h, r]) => {
        if (controller.signal.aborted) return;
        setHistory(h.status === "fulfilled" ? h.value : []);
        setRefs(r.status === "fulfilled" ? r.value : []);
        const failed = [h, r].find((x) => x.status === "rejected") as PromiseRejectedResult | undefined;
        setDetailError(failed ? describe(failed.reason) : null);
      },
    );
    return () => controller.abort();
  }, [name, version]);

  useTabReady("variables", variables !== null, loadErrors);

  const saved = useCallback((next: Variable) => {
    setVariables((list) => (list ?? []).map((v) => (v.name === next.name ? next : v)));
  }, []);

  return (
    <main id="main" className="vars" tabIndex={-1}>
      <h1 className="vars__title">Variables</h1>
      {loadErrors.map((e) => (
        <p key={e} className="notice notice--error" role="alert">
          {e}
        </p>
      ))}
      <div className="vars__body">
        <nav className="vars-list" aria-label="Variables">
          {all.map((v) => (
            <Link
              key={v.name}
              to={`?name=${encodeURIComponent(v.name)}`}
              replace
              className="vars-row"
              aria-current={v.name === selected?.name ? "true" : undefined}
            >
              <span className="vars-row__name">{v.name}</span>
              <span className="vars-row__value">{valueText(v.value)}</span>
            </Link>
          ))}
          {variables !== null && all.length === 0 && loadErrors.length === 0 ? (
            <p className="vars-note">No variables yet. An admin can create one with the CLI or the API.</p>
          ) : null}
        </nav>
        {selected ? (
          <Detail
            variable={selected}
            admin={admin}
            history={history}
            refs={refs}
            detailError={detailError}
            onSaved={saved}
          />
        ) : null}
      </div>
    </main>
  );
}

export default Variables;
