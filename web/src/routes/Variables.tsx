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

/** One editable list item: the stored item (`orig`, absent for a new row) and its text. */
export interface Row {
  id: number;
  orig?: Scalar;
  text: string;
}

let nextRowId = 1;
const newRowId = () => nextRowId++;

/** One row per stored item; each starts as its exact stored text. */
export const rowsOf = (list: Scalar[]): Row[] =>
  list.map((orig) => ({ id: newRowId(), orig, text: itemText(orig) }));

/**
 * The list a set of rows would save, lossless by construction: a row whose text still equals
 * its stored item's text yields that stored item itself (type and exact string). Only an edited
 * row is re-typed: a number stays a number (`invalid` lists the rows whose text no longer is
 * one), a boolean takes true/false, anything else is the exact text. A new row is typed like
 * a uniform list: numeric or boolean (other text is then refused in `invalid`); otherwise it is
 * the exact text. A new blank row is dropped.
 */
export function valueOfRows(current: Scalar[], rows: Row[]): { value: Scalar[]; invalid: number[] } {
  const sample = current.find((v) => v !== null && v !== "");
  const uniform = sample !== undefined && current.every((v) => typeof v === typeof sample);
  const value: Scalar[] = [];
  const invalid: number[] = [];
  rows.forEach((row, i) => {
    if (row.orig !== undefined && row.text === itemText(row.orig)) {
      value.push(row.orig);
    } else if (row.orig === undefined && row.text === "") {
      // a blank new row is not an item
    } else if (typeof (row.orig ?? (uniform ? sample : undefined)) === "number") {
      if (NUMBER.test(row.text.trim())) value.push(Number(row.text));
      else invalid.push(i);
    } else if (row.text === "true" || row.text === "false") {
      if (typeof (row.orig ?? (uniform ? sample : undefined)) === "boolean") value.push(row.text === "true");
      else value.push(row.text);
    } else if (row.orig === undefined && uniform && typeof sample === "boolean") {
      invalid.push(i); // a new row in a boolean list must be true or false
    } else {
      value.push(row.text);
    }
  });
  return { value, invalid };
}

/** A scalar variable's value from its text (an untouched text keeps the stored value). */
function scalarOf(current: Scalar, text: string): { value: Scalar; invalid: boolean } {
  if (text === itemText(current)) return { value: current, invalid: false };
  if (typeof current === "number") {
    return NUMBER.test(text.trim()) ? { value: Number(text), invalid: false } : { value: current, invalid: true };
  }
  if (typeof current === "boolean" && (text === "true" || text === "false")) {
    return { value: text === "true", invalid: false };
  }
  return { value: text, invalid: false };
}

interface DetailProps {
  variable: Variable;
  admin: boolean;
  /** History and referrers of THIS variable; null while they load. */
  details: { history: VariableVersion[]; refs: VariableRef[]; error: string | null } | null;
  onSaved: (saved: Variable) => void;
}

function ValueEditor({ variable, admin, onSaved }: Readonly<Pick<DetailProps, "variable" | "admin" | "onSaved">>) {
  const stored = variable.value;
  const [rows, setRows] = useState<Row[]>(Array.isArray(stored) ? rowsOf(stored) : []);
  const [text, setText] = useState(Array.isArray(stored) ? "" : itemText(stored));
  const [pending, setPending] = useState(false);
  const [refused, setRefused] = useState<ApiError | null>(null);
  const reset = () => {
    setRows(Array.isArray(stored) ? rowsOf(stored) : []);
    setText(Array.isArray(stored) ? "" : itemText(stored));
  };

  if (!admin) {
    return (
      <>
        {Array.isArray(stored) ? (
          <ul className="vars-items" aria-label="Current items">
            {stored.map((item, i) => (
              <li key={`${itemText(item)}-${i}`}>{itemText(item)}</li>
            ))}
          </ul>
        ) : (
          <p className="vars-scalar">{valueText(stored)}</p>
        )}
        <p className="vars-note">Only admins can change variables.</p>
      </>
    );
  }

  let next: VariableValue;
  let badRows: number[] = [];
  if (Array.isArray(stored)) {
    const built = valueOfRows(stored, rows);
    next = built.value;
    badRows = built.invalid;
  } else {
    const built = scalarOf(stored, text);
    next = built.value;
    if (built.invalid) badRows = [0];
  }
  const unchanged = JSON.stringify(next) === JSON.stringify(stored);
  const blocked = unchanged || pending || badRows.length > 0;
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (blocked) return;
    setPending(true);
    setRefused(null);
    try {
      onSaved(await putVariable(variable.name, next, variable.description));
    } catch (err) {
      setRefused(err instanceof ApiError ? err : new ApiError(0, "unreachable", describe(err)));
    } finally {
      setPending(false);
    }
  };
  const edit = (id: number, value: string) =>
    setRows((rs) => rs.map((r) => (r.id === id ? { ...r, text: value } : r)));
  return (
    <form className="vars-form" onSubmit={submit} aria-label={`Edit ${variable.name}`}>
      {Array.isArray(stored) ? (
        <fieldset className="vars-rows plain-group">
          <legend>Items</legend>
          {rows.map((row, i) => (
            <div key={row.id} className="vars-rows__row">
              <input
                aria-label={`Item ${i + 1}`}
                aria-invalid={badRows.includes(i) || undefined}
                value={row.text}
                placeholder={row.orig === null ? "(empty)" : undefined}
                spellCheck={false}
                onChange={(e) => edit(row.id, e.target.value)}
              />
              <button
                type="button"
                className="vars-btn"
                aria-label={`Remove item ${i + 1}`}
                onClick={() => setRows((rs) => rs.filter((r) => r.id !== row.id))}
              >
                Remove
              </button>
            </div>
          ))}
          <button type="button" className="vars-btn" onClick={() => setRows((rs) => [...rs, { id: newRowId(), text: "" }])}>
            Add item
          </button>
        </fieldset>
      ) : (
        <label>
          <span>Value</span>
          <input value={text} aria-invalid={badRows.length > 0 || undefined} onChange={(e) => setText(e.target.value)} />
        </label>
      )}
      {badRows.length > 0 ? <p className="vars-note">That has to be a number.</p> : null}
      {refused ? <GuidedNotice error={refused} /> : null}
      <div className="vars-actions">
        <button type="submit" className="vars-btn vars-btn--primary" disabled={blocked}>
          Save new version
        </button>
        <button type="button" className="vars-btn" disabled={unchanged || pending} onClick={reset}>
          Revert
        </button>
      </div>
    </form>
  );
}

function Detail({ variable, admin, details, onSaved }: Readonly<DetailProps>) {
  const newestFirst = [...(details?.history ?? [])].reverse();
  const refs = details?.refs ?? [];
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
      <ValueEditor key={`${variable.name}@${variable.version}`} variable={variable} admin={admin} onSaved={onSaved} />
      {details?.error ? (
        <p className="notice notice--error" role="alert">
          {details.error}
        </p>
      ) : null}
      {details ? (
        <>
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
        </>
      ) : (
        <p className="vars-note">Loading history and rules…</p>
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
  /** The fetched details, tagged with the variable they belong to. */
  const [fetched, setFetched] = useState<{
    name: string;
    history: VariableVersion[];
    refs: VariableRef[];
    error: string | null;
  } | null>(null);

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
    Promise.allSettled([getVariableHistory(name, controller.signal), getVariableRefs(name, controller.signal)]).then(
      ([h, r]) => {
        if (controller.signal.aborted) return;
        const failed = [h, r].find((x) => x.status === "rejected") as PromiseRejectedResult | undefined;
        setFetched({
          name,
          history: h.status === "fulfilled" ? h.value : [],
          refs: r.status === "fulfilled" ? r.value : [],
          error: failed ? describe(failed.reason) : null,
        });
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
            details={fetched && fetched.name === selected.name ? fetched : null}
            onSaved={saved}
          />
        ) : null}
      </div>
    </main>
  );
}

export default Variables;
