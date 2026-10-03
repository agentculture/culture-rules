import { API_ROOT, ApiError } from "./client";
import type { ErrorEnvelope, ItemList, Rule, RunSummary } from "./types";

/**
 * The Rules tab's API calls (api/openapi.json): the write verbs the shared
 * client does not carry, built on its `API_ROOT` and `ApiError`. Like the
 * client, no call attaches a credential: the edge (or the dev proxy) does.
 */

/** A rule as the API stores it: `Rule` plus the `supersedes` relationship. */
export interface RuleDoc extends Rule {
  supersedes?: string[];
}

/** An open human ask (`asks` collection, culture_rules/actors/human.py). */
export interface Ask {
  id: string;
  run_id: string;
  question: string;
  options: string[] | null;
  deadline?: string;
  status?: string;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_ROOT}${path}`, {
      method,
      signal,
      headers: {
        accept: "application/json",
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "unreachable", `cannot reach the culture-rules API at ${API_ROOT}`);
  }
  const text = await response.text();
  let parsed: unknown = null;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = null;
  }
  if (!response.ok) {
    const envelope = parsed as Partial<ErrorEnvelope> | null;
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? "http_error",
      envelope?.error?.message ?? `${response.status} ${response.statusText}`.trim(),
    );
  }
  return parsed as T;
}

const enc = encodeURIComponent;

/** A write answers the stored rule; fall back to what was sent if it answers less. */
function asRule(answer: unknown, fallback: RuleDoc): RuleDoc {
  const doc = answer as Partial<RuleDoc> | null;
  return doc && typeof doc === "object" && typeof doc.id === "string" && doc.trigger
    ? (doc as RuleDoc)
    : fallback;
}

export async function setRuleEnabled(rule: RuleDoc, enabled: boolean): Promise<RuleDoc> {
  const answer = await request<unknown>(
    "POST",
    `/rules/${enc(rule.id)}/${enabled ? "enable" : "disable"}`,
  );
  return asRule(answer, { ...rule, enabled });
}

export async function updateRule(rule: RuleDoc): Promise<RuleDoc> {
  return asRule(await request<unknown>("PUT", `/rules/${enc(rule.id)}`, rule), rule);
}

export async function createRule(rule: RuleDoc): Promise<RuleDoc> {
  return asRule(await request<unknown>("POST", "/rules", rule), rule);
}

/** Soft delete: the API keeps the document so `restoreRule` can bring it back. */
export async function deleteRule(id: string): Promise<void> {
  await request<unknown>("DELETE", `/rules/${enc(id)}`);
}

export async function restoreRule(rule: RuleDoc): Promise<RuleDoc> {
  return asRule(await request<unknown>("POST", `/rules/${enc(rule.id)}/restore`), rule);
}

export async function listWaitingRuns(ruleId: string, signal?: AbortSignal) {
  const answer = await request<ItemList<RunSummary>>(
    "GET",
    `/runs?status=waiting&rule_id=${enc(ruleId)}`,
    undefined,
    signal,
  );
  // A server that ignores the filter still answers every run; keep only the waiting ones.
  return answer.items.filter((r) => r.status === "waiting" && r.rule_id === ruleId);
}

/**
 * Open asks of one run. The HTTP API has no list endpoint for asks yet
 * (only `POST /asks/{id}/answer`); until it does, a 404/405 reads as
 * "nothing to answer here" rather than as a failure.
 */
export async function listAsks(runId: string, signal?: AbortSignal): Promise<Ask[]> {
  try {
    const answer = await request<ItemList<Ask>>(
      "GET",
      `/asks?run_id=${enc(runId)}&status=open`,
      undefined,
      signal,
    );
    return answer.items.filter((a) => (a.status ?? "open") === "open");
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 405)) return [];
    throw err;
  }
}

export async function answerAsk(id: string, answer: string): Promise<void> {
  await request<unknown>("POST", `/asks/${enc(id)}/answer`, { answer });
}
