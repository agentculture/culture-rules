import { builtinOf, type BuiltinCatalogue, type BuiltinSpec } from "../api/builtins";
import { BUILTINS_DATA } from "./builtins.data";

/**
 * What `GET /steps/builtins` serves for the built-in code steps, a snapshot of the Python
 * registry (tests/server/test_builtins_api.py checks it is current: regenerate with
 * `python3 scripts/export-builtins.py --write`).
 */
export const BUILTIN_CATALOGUE = BUILTINS_DATA as BuiltinCatalogue;

/** A built-in's catalogue entry (tests). */
export const builtinSpec = (name: string): BuiltinSpec => {
  const spec = builtinOf(BUILTIN_CATALOGUE, name);
  if (!spec) throw new Error(`no ${name} in the builtin catalogue`);
  return spec;
};
