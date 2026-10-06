import { items, request } from "./client";

/**
 * Shared variables (api/openapi.json: `/variables`, `/variables/{name}`,
 * `/history`, `/refs`). A value is a JSON scalar or a flat list of scalars; a
 * rule reads one through a `{"var": name}` operand. Writes are admin only
 * (403 otherwise) and append a new version.
 */
export type Scalar = string | number | boolean | null;
export type VariableValue = Scalar | Scalar[];

/** One variable at its latest version (`GET /variables`, `GET /variables/{name}`). */
export interface Variable {
  id: string;
  name: string;
  value: VariableValue;
  version: number;
  updated_by: string;
  updated_at: string;
  description?: string | null;
}

/** One entry of `GET /variables/{name}/history` (oldest first). */
export type VariableVersion = Variable;

/** A rule that references a variable (`GET /variables/{name}/refs`). */
export interface VariableRef {
  id: string;
  name: string;
  enabled: boolean;
}

const enc = encodeURIComponent;

export const listVariables = (signal?: AbortSignal) => items<Variable>("/variables", signal);

export const getVariableHistory = (name: string, signal?: AbortSignal) =>
  items<VariableVersion>(`/variables/${enc(name)}/history`, signal);

export const getVariableRefs = (name: string, signal?: AbortSignal) =>
  items<VariableRef>(`/variables/${enc(name)}/refs`, signal);

/** `PUT /variables/{name}`: appends a version; answers the new latest. */
export const putVariable = (name: string, value: VariableValue, description?: string | null) =>
  request<Variable>("PUT", `/variables/${enc(name)}`, {
    value,
    ...(description ? { description } : {}),
  });
