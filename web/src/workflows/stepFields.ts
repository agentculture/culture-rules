import type { Actor, Retry } from "../api/workflows";

/** Guidance codes (api/guidance.ts) a field can fail with. */
export type FieldError = "invalid_value" | "range";

export type Parsed = { ok: true; value: number | null } | { ok: false; code: FieldError };

/**
 * Parse a numeric text field. Empty is "unset" (`null`); anything that is not a
 * finite number is `invalid_value`; a number outside the bounds is `range`.
 */
export function parseNumber(
  text: string,
  opts: { integer?: boolean; min: number; exclusiveMin?: boolean },
): Parsed {
  const t = text.trim();
  if (t === "") return { ok: true, value: null };
  const n = Number(t);
  if (!Number.isFinite(n)) return { ok: false, code: "invalid_value" };
  if (opts.integer && !Number.isInteger(n)) return { ok: false, code: "range" };
  if (opts.exclusiveMin ? n <= opts.min : n < opts.min) return { ok: false, code: "range" };
  return { ok: true, value: n };
}

export const parseTimeout = (text: string) => parseNumber(text, { min: 0, exclusiveMin: true });

export type RetryKey = keyof Retry;

export const RETRY_FIELDS: { key: RetryKey; label: string; hint: string; opts: Parameters<typeof parseNumber>[1] }[] = [
  { key: "max_attempts", label: "Max attempts", hint: "a whole number, 1 or more", opts: { integer: true, min: 1 } },
  { key: "backoff_s", label: "Backoff (seconds)", hint: "0 or more", opts: { min: 0 } },
  { key: "backoff_multiplier", label: "Backoff multiplier", hint: "1 or more", opts: { min: 1 } },
];

export const RETRY_DEFAULTS: Required<Retry> = { max_attempts: 1, backoff_s: 0, backoff_multiplier: 1 };

/**
 * Fold one edited retry field into the policy. Unset fields stay as they are
 * (so unknown keys survive); when nothing is left the policy is unset (`null`).
 */
export function withRetryField(retry: Retry | null | undefined, key: RetryKey, value: number | null): Retry | null {
  const next: Record<string, unknown> = { ...(retry ?? {}) };
  if (value === null) delete next[key];
  else next[key] = value;
  if (value !== null) for (const f of RETRY_FIELDS) next[f.key] ??= RETRY_DEFAULTS[f.key];
  return Object.keys(next).length === 0 ? null : (next as Retry);
}

/** The runner commands an actor declares, or undefined when it is not a runner with any. */
export function runnerCommands(actors: readonly Actor[], actorId: string | null | undefined) {
  if (!actorId) return undefined;
  const a = actors.find((x) => x.id === actorId);
  if (a?.kind !== "runner") return undefined;
  const commands = a.params?.commands;
  return commands && Object.keys(commands).length > 0 ? commands : undefined;
}

export type Config = Record<string, unknown>;

export const isScalar = (v: unknown): v is string | number | boolean | null =>
  v === null || ["string", "number", "boolean"].includes(typeof v);

export function parseConfigJson(text: string): { ok: true; value: Config } | { ok: false } {
  try {
    const v: unknown = JSON.parse(text);
    if (v !== null && typeof v === "object" && !Array.isArray(v)) return { ok: true, value: v as Config };
  } catch {
    /* fall through */
  }
  return { ok: false };
}
