import { useCallback, useEffect, useState } from "react";
import { getJson } from "./client";

/**
 * The action catalogue: `GET /actions/kinds` (api/openapi.json, `ActionKindList`), the kinds the
 * API's extension registry holds, in catalogue order, with the typed params and the hints a
 * picker needs. The editor keeps no table of its own: a kind an extension registers shows up here
 * with no editor change.
 */
export interface ActionChoice {
  value: string;
  label: string;
}

export interface ActionParam {
  name: string;
  /** str | int | bool | dict | list | any */
  type: string;
  required: boolean;
  /** How a picker names it; empty: from the name. */
  label: string;
  /** multiline | json | number | buttons | command | command_args | discord_guild | discord_channel | hidden; empty: by type. */
  widget: string;
  /** A closed set of values, or empty. */
  choices: ActionChoice[];
  /** The chosen actor's params key that lists (or, as a mapping, names) the values. */
  options_from: string;
  /** The prompt of the empty choice. */
  choose: string;
  /** Shown only when the chosen actor has this param set. */
  needs_actor_param: string;
  /** Not required of a stored step, but the editor asks for it. */
  needed: boolean;
}

/** One alternative of "which actors can do it": every condition holds for an enabled actor. */
export interface ActorClause {
  kinds?: string[];
  not_kinds?: string[];
  surface?: string;
  declares_action?: boolean;
  has?: string[];
  has_any?: string[];
}

export interface ActionKindSpec {
  kind: string;
  label: string;
  summary: string;
  lenient: boolean;
  aliases: string[];
  params: ActionParam[];
  any_of: string[][];
  actors: ActorClause[];
  actorless: boolean;
}

export interface ActionCatalogue {
  items: ActionKindSpec[];
}

/** The catalogue entry for `name`, a legacy alias resolved; undefined for a kind not in it. */
export function kindOf(catalogue: ActionCatalogue | undefined, name: string): ActionKindSpec | undefined {
  return catalogue?.items.find((k) => k.kind === name || k.aliases.includes(name));
}

export const paramOf = (spec: ActionKindSpec | undefined, name: string): ActionParam | undefined =>
  spec?.params.find((p) => p.name === name);

export const getActionKinds = (signal?: AbortSignal) => getJson<ActionCatalogue>("/actions/kinds", signal);

let loaded: Promise<ActionCatalogue> | null = null;

/** The catalogue, fetched once and kept; a failure is not kept, so the next call asks again. */
export function loadActionCatalogue(): Promise<ActionCatalogue> {
  if (!loaded) {
    const attempt = getActionKinds();
    loaded = attempt;
    attempt.catch(() => {
      if (loaded === attempt) loaded = null;
    });
  }
  return loaded;
}

/** Forget the kept catalogue (tests). */
export function resetActionCatalogue(): void {
  loaded = null;
}

export interface CatalogueState {
  catalogue?: ActionCatalogue;
  failed: boolean;
  retry: () => void;
}

/** The catalogue from the API, loaded once for the whole app; `failed` until `retry` succeeds. */
export function useActionCatalogue(): CatalogueState {
  const [catalogue, setCatalogue] = useState<ActionCatalogue>();
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setFailed(false);
    loadActionCatalogue().then(
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
