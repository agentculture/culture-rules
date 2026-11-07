import { useCallback, useEffect, useState } from "react";
import { getJson } from "./client";
import type { PortType } from "./workflows";

/**
 * The builtin catalogue: `GET /steps/builtins` (api/openapi.json, `BuiltinList`), the built-in
 * code steps the API's extension registry holds, in registration order, with the label a picker
 * shows and the typed ports an editor pre-fills. The editor keeps no table of its own: a builtin
 * an extension registers shows up here with no editor change.
 */
export interface BuiltinPort {
  name: string;
  type: PortType;
  required: boolean;
}

export interface BuiltinSpec {
  /** What a code step's `config.builtin` names. */
  name: string;
  /** How a picker names it (its describe phrase when none was registered). */
  label: string;
  /** The typed ports it takes, in order; empty: none declared. */
  inputs: BuiltinPort[];
  /** The typed ports it gives, in order; empty: none declared. */
  outputs: BuiltinPort[];
}

export interface BuiltinCatalogue {
  items: BuiltinSpec[];
}

/** The catalogue entry for `name`; undefined for a builtin not in it. */
export function builtinOf(catalogue: BuiltinCatalogue | undefined, name: string): BuiltinSpec | undefined {
  return catalogue?.items.find((b) => b.name === name);
}

export const getBuiltins = (signal?: AbortSignal) => getJson<BuiltinCatalogue>("/steps/builtins", signal);

let loaded: Promise<BuiltinCatalogue> | null = null;

/** The catalogue, fetched once and kept; a failure is not kept, so the next call asks again. */
export function loadBuiltinCatalogue(): Promise<BuiltinCatalogue> {
  if (!loaded) {
    const attempt = getBuiltins();
    loaded = attempt;
    attempt.catch(() => {
      if (loaded === attempt) loaded = null;
    });
  }
  return loaded;
}

/** Forget the kept catalogue (tests). */
export function resetBuiltinCatalogue(): void {
  loaded = null;
}

export interface BuiltinCatalogueState {
  catalogue?: BuiltinCatalogue;
  failed: boolean;
  retry: () => void;
}

/** The catalogue from the API, loaded once for the whole app; `failed` until `retry` succeeds. */
export function useBuiltinCatalogue(): BuiltinCatalogueState {
  const [catalogue, setCatalogue] = useState<BuiltinCatalogue>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(false);
    loadBuiltinCatalogue().then(
      (c) => live && setCatalogue(c),
      () => live && setFailed(true),
    );
    return () => {
      live = false;
    };
  }, [attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { catalogue, failed, retry };
}
