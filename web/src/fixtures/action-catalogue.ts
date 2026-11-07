import { kindOf, type ActionCatalogue, type ActionKindSpec, type ActionParam } from "../api/actions";
import { ACTION_KINDS_DATA } from "./action-kinds.data";

/**
 * What `GET /actions/kinds` serves for the built-in kinds, a snapshot of the Python catalogue
 * (tests/server/test_action_kinds_api.py checks it is current: regenerate with
 * `python3 scripts/export-action-kinds.py --write`).
 */
export const ACTION_CATALOGUE = ACTION_KINDS_DATA as ActionCatalogue;

/** A built-in kind's catalogue entry (tests). */
export const kindSpec = (name: string): ActionKindSpec => {
  const spec = kindOf(ACTION_CATALOGUE, name);
  if (!spec) throw new Error(`no ${name} in the catalogue`);
  return spec;
};

/** A built-in kind's params by name (tests). */
export const paramsOf = (name: string): Record<string, ActionParam> =>
  Object.fromEntries(kindSpec(name).params.map((p) => [p.name, p]));
