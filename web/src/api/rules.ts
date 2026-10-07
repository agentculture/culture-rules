import { ApiError, listRuns, request } from "./client";
import type {
  ActiveRun,
  Ask,
  ItemList,
  Rule,
  RuleHistoryItem,
  RunSummary,
  StopRunsResult,
} from "./types";

export type {
  Action,
  ActionKind,
  EventTriggerParams,
  ManualTriggerParams,
  ProbeMode,
  ProbeTriggerParams,
  ScheduleTriggerParams,
  Trigger,
  TriggerKind,
  TypedTrigger,
} from "./types";

/**
 * The Rules tab's API calls (api/openapi.json), built on the shared
 * `request` helper in client.ts. Like the client, no call attaches a
 * credential: the edge (or the dev proxy) does.
 */

/** A rule as the API stores it (`supersedes` included). */
export type RuleDoc = Rule;
export type { ActiveRun, Ask, RuleHistoryItem, StopRunsResult };

const enc = encodeURIComponent;

/** A write answers the stored rule; fall back to what was sent if it answers less. */
function asRule(answer: unknown, fallback: RuleDoc): RuleDoc {
  const doc = answer as Partial<RuleDoc> | null;
  return doc && typeof doc === "object" && typeof doc.id === "string" && doc.trigger
    ? (doc as RuleDoc)
    : fallback;
}

/** What a toggle answers: the stored rule, and (on disable) the runs still going (d17). */
export interface RuleToggle {
  rule: RuleDoc;
  activeRuns: ActiveRun[];
  activeRunsTotal: number;
}

/**
 * Split a disable answer: `active_runs` / `active_runs_total` are response-only
 * (never stored), so they are taken off the rule before it goes back in the list.
 */
function splitActiveRuns(answer: unknown): { doc: unknown; runs: ActiveRun[]; total: number } {
  if (!answer || typeof answer !== "object") return { doc: answer, runs: [], total: 0 };
  const { active_runs, active_runs_total, ...doc } = answer as Record<string, unknown>;
  const runs = Array.isArray(active_runs) ? (active_runs as ActiveRun[]) : [];
  const total = typeof active_runs_total === "number" ? active_runs_total : runs.length;
  return { doc, runs, total };
}

export async function setRuleEnabled(rule: RuleDoc, enabled: boolean): Promise<RuleToggle> {
  const answer = await request<unknown>(
    "POST",
    `/rules/${enc(rule.id)}/${enabled ? "enable" : "disable"}`,
  );
  const { doc, runs, total } = splitActiveRuns(answer);
  return { rule: asRule(doc, { ...rule, enabled }), activeRuns: runs, activeRunsTotal: total };
}

/**
 * `POST /rules/{id}/stop-runs` with `apply: true`: cancel every active run of
 * a disabled rule (the 'Stop N current runs?' approval, d17).
 */
export async function stopRuleRuns(ruleId: string): Promise<StopRunsResult> {
  return request<StopRunsResult>("POST", `/rules/${enc(ruleId)}/stop-runs`, { apply: true });
}

export async function updateRule(rule: RuleDoc): Promise<RuleDoc> {
  const { doc } = splitActiveRuns(await request<unknown>("PUT", `/rules/${enc(rule.id)}`, rule));
  return asRule(doc, rule);
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

/** `GET /asks?run_id=&status=open`: the open asks of one run. */
export async function listAsks(runId: string, signal?: AbortSignal): Promise<Ask[]> {
  const answer = await request<ItemList<Ask>>(
    "GET",
    `/asks?run_id=${enc(runId)}&status=open`,
    undefined,
    signal,
  );
  return answer.items.filter((a) => (a.status ?? "open") === "open");
}

export async function answerAsk(id: string, answer: string): Promise<void> {
  await request<unknown>("POST", `/asks/${enc(id)}/answer`, { answer });
}

/**
 * `GET /rules/{id}/history`: the rule's runs and recorded skips, newest
 * first. An API without the route (404 `not_found`) falls back to the
 * rule's runs alone.
 */
export async function getRuleHistory(
  ruleId: string,
  limit: number,
  signal?: AbortSignal,
): Promise<RuleHistoryItem[]> {
  try {
    const answer = await request<ItemList<RuleHistoryItem>>(
      "GET",
      `/rules/${enc(ruleId)}/history?limit=${limit}`,
      undefined,
      signal,
    );
    return answer.items;
  } catch (err) {
    if (!(err instanceof ApiError && err.status === 404)) throw err;
    const runs = await listRuns({ rule_id: ruleId, limit }, signal);
    return runs.map((r) => ({ kind: "run" as const, at: r.created_at, ...r }));
  }
}
