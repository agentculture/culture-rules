import type { ActiveRun, Rule, RunSummary } from "../api/types";
import type { Variable } from "../api/variables";
import type { WorkflowDef } from "../api/workflows";
import {
  ACTORS,
  MACHINES,
  RULES,
  VARIABLES,
  VARIABLE_REFS,
  VARIABLE_VERSIONS,
  WHOAMI,
  WORKFLOWS,
  runsFor,
} from "../fixtures/rules-fixture";

/**
 * A stateful, in-memory culture-rules API (api/openapi.json shapes) for the
 * Rules tab's tests: vitest wraps it in a `fetch` stub, the Playwright suite
 * in request interception. Pure, so both share one behaviour. Soft delete
 * moves a rule to `trash`; `restore` brings it back (the API's own pair).
 */
export interface FakeAsk {
  id: string;
  run_id: string;
  question: string;
  options: string[] | null;
  status: "open" | "answered";
  answer?: unknown;
}

/** A persisted skip (`rule_decisions`, culture_rules/engine/decisions.py). */
export interface FakeDecision {
  rule_id: string;
  event_id: string;
  reason: string;
  by: string[];
  message: string;
  at: string;
  host: string;
}

export interface FakeApi {
  rules: Rule[];
  workflows: WorkflowDef[];
  workflowTrash: WorkflowDef[];
  decisions: FakeDecision[];
  trash: Rule[];
  /** Every variable version, oldest first (the latest of each name is what lists show). */
  variableVersions: Variable[];
  asks: FakeAsk[];
  waitingRuns: RunSummary[];
  /** Each rule's active runs: a disable reports them, `stop-runs` cancels them (d17). */
  activeRuns: Record<string, ActiveRun[]>;
  calls: { method: string; path: string; body?: unknown }[];
  /** Make the next request to `method path` fail with this status. */
  failNext: Record<string, { status: number; code: string; message: string }>;
  now: number;
}

export interface FakeResponse {
  status: number;
  body: unknown;
}

export function createFakeApi(now = Date.now()): FakeApi {
  return {
    rules: structuredClone(RULES),
    workflows: structuredClone(WORKFLOWS),
    workflowTrash: [],
    decisions: [],
    trash: [],
    variableVersions: structuredClone([...VARIABLE_VERSIONS, VARIABLES[1]]),
    asks: [],
    waitingRuns: [],
    activeRuns: {},
    calls: [],
    failNext: {},
    now,
  };
}

/** One pending human ask on the selected rule, as the board's 'answerable in context' case. */
export function withPendingAsk(api: FakeApi, ruleId = "build-and-publish"): FakeApi {
  api.waitingRuns = [
    {
      id: "run-wait",
      status: "waiting",
      rule_id: ruleId,
      workflow_id: "build-image",
      started_by: "trigger",
      created_at: new Date(api.now - 5 * 60_000).toISOString(),
      finished_at: null,
    },
  ];
  api.asks = [
    {
      id: "ask_1",
      run_id: "run-wait",
      question: "Ship this build to production?",
      options: ["approve", "reject"],
      status: "open",
    },
  ];
  return api;
}

/** `n` runs of `ruleId` still going, so disabling it asks 'Stop N current runs?'. */
export function withActiveRuns(api: FakeApi, ruleId: string, n: number): FakeApi {
  api.activeRuns[ruleId] = Array.from({ length: n }, (_, i) => ({
    id: `run-active-${i + 1}`,
    status: "running",
    started_at: new Date(api.now - (i + 1) * 60_000).toISOString(),
  }));
  return api;
}

const json = (status: number, body: unknown): FakeResponse => ({ status, body });
const error = (status: number, code: string, message: string) =>
  json(status, { error: { code, message, errors: [] } });

function handleGet(api: FakeApi, path: string, query: URLSearchParams): FakeResponse {
  if (path === "/whoami") return json(200, WHOAMI);
  if (path === "/rules") return json(200, { items: api.rules });
  if (path === "/actors") return json(200, { items: ACTORS });
  if (path === "/machines") return json(200, { items: MACHINES });
  if (path === "/workflows") return json(200, { items: api.workflows });
  const rule = /^\/rules\/([^/]+)$/.exec(path);
  if (rule) {
    const found = api.rules.find((r) => r.id === decodeURIComponent(rule[1]));
    return found ? json(200, found) : error(404, "not_found", path);
  }
  const described = /^\/rules\/([^/]+)\/describe$/.exec(path);
  if (described) return describeRule(api, described[1]);
  if (path === "/runs") return listRuns(api, query);
  const history = /^\/rules\/([^/]+)\/history$/.exec(path);
  if (history) return ruleHistory(api, decodeURIComponent(history[1]), query);
  const variable = /^\/variables\/([^/]+)(?:\/(history|refs))?$/.exec(path);
  if (path === "/variables") return json(200, { items: latestVariables(api) });
  if (variable) return variableRead(api, decodeURIComponent(variable[1]), variable[2]);
  if (path === "/asks") return openAsks(api, query);
  return error(404, "not_found", path);
}

/** A stand-in for culture_rules/model/describe.py: the trigger and the action, in words. */
function describeRule(api: FakeApi, rawId: string): FakeResponse {
  const rule = api.rules.find((r) => r.id === decodeURIComponent(rawId));
  if (!rule) return error(404, "not_found", `rules/${rawId} does not exist`);
  const params = (rule.trigger.params ?? {}) as Record<string, unknown>;
  const type = typeof params.type === "string" ? params.type : rule.trigger.kind;
  const entries = [
    { label: "When", text: type, depth: 0 },
    { label: "Then", text: rule.action.kind, depth: 0 },
  ];
  return json(200, { id: rule.id, kind: "rule", lines: entries.map((e) => `${e.label} ${e.text}`), entries });
}

/** `GET /runs`: the waiting runs alone for `status=waiting`, else every run (of `rule_id`). */
function listRuns(api: FakeApi, query: URLSearchParams): FakeResponse {
  const status = query.get("status");
  if (status === "waiting") return json(200, { items: api.waitingRuns });
  const all = [...api.waitingRuns, ...runsFor(api.now)];
  const rule = query.get("rule_id");
  return json(200, { items: all.filter((r) => !rule || r.rule_id === rule) });
}

/** `GET /asks`: the open asks (of `run_id`). */
function openAsks(api: FakeApi, query: URLSearchParams): FakeResponse {
  const run = query.get("run_id");
  return json(200, {
    items: api.asks.filter((a) => a.status === "open" && (!run || a.run_id === run)),
  });
}

const latestVariables = (api: FakeApi): Variable[] => {
  const latest = new Map<string, Variable>();
  for (const v of api.variableVersions) latest.set(v.name, v);
  return [...latest.values()];
};

/** `/variables/{name}[/history|/refs]` over the fixture's `trusted_authors` and `max_fixes`. */
function variableRead(api: FakeApi, name: string, part: string | undefined): FakeResponse {
  const latest = latestVariables(api).find((v) => v.name === name);
  if (!latest) return error(404, "not_found", `variables/${name} does not exist`);
  if (part === "history") {
    return json(200, { items: api.variableVersions.filter((v) => v.name === name) });
  }
  if (part === "refs") return json(200, { items: name === "trusted_authors" ? VARIABLE_REFS : [] });
  return json(200, latest);
}

/** `PUT /variables/{name}`: appends a version (the fake caller is the fixture's admin). */
function variableWrite(api: FakeApi, name: string, body: unknown): FakeResponse {
  const { value, description } = body as { value: Variable["value"]; description?: string };
  const last = api.variableVersions.findLast((v) => v.name === name);
  const next: Variable = {
    id: name,
    name,
    value,
    version: (last?.version ?? 0) + 1,
    updated_by: WHOAMI.identity,
    updated_at: new Date(api.now).toISOString(),
    // like the backend (put_variable): an omitted description is stored as null
    description: description ?? null,
  };
  api.variableVersions.push(next);
  return json(200, next);
}

function ruleHistory(api: FakeApi, id: string, query: URLSearchParams): FakeResponse {
  if (!api.rules.some((r) => r.id === id)) return error(404, "not_found", `rule ${id} does not exist`);
  const runs = [...api.waitingRuns, ...runsFor(api.now)]
    .filter((r) => r.rule_id === id)
    .map((r) => ({ kind: "run", at: r.created_at, ...r }));
  const skips = api.decisions
    .filter((d) => d.rule_id === id)
    .map((d) => ({ kind: "decision", ...d }));
  const limit = Number(query.get("limit") ?? 20);
  const items = [...runs, ...skips]
    .sort((a, b) => String(b.at ?? "").localeCompare(String(a.at ?? "")))
    .slice(0, limit);
  return json(200, { items });
}

/** `/rules/{id}[/enable|/disable|/restore|/stop-runs]` writes; null when the method doesn't apply. */
function handleRuleWrite(
  api: FakeApi,
  method: string,
  id: string,
  verb: string | undefined,
  body: unknown,
): FakeResponse | null {
  if (method === "POST" && verb === "restore") return restoreRule(api, id);
  const found = api.rules.find((r) => r.id === id);
  if (!found) return error(404, "not_found", `rule ${id} does not exist`);
  if (method === "POST" && (verb === "enable" || verb === "disable")) return toggleRule(api, found, verb);
  if (method === "POST" && verb === "stop-runs") return stopRuns(api, found, body);
  if (method === "PUT") {
    Object.assign(found, body as Rule, { id });
    return json(200, found);
  }
  if (method === "DELETE") {
    api.rules.splice(api.rules.indexOf(found), 1);
    api.trash.push(found);
    return json(200, { id, deleted: true });
  }
  return null;
}

/** `POST /rules/{id}/restore`: back from the trash. */
function restoreRule(api: FakeApi, id: string): FakeResponse {
  const at = api.trash.findIndex((r) => r.id === id);
  if (at < 0) return error(404, "not_found", id);
  api.rules.push(api.trash.splice(at, 1)[0]);
  return json(200, api.rules.at(-1));
}

/** `POST /rules/{id}/enable|disable`; a disable lists the rule's active runs. */
function toggleRule(api: FakeApi, found: Rule, verb: "enable" | "disable"): FakeResponse {
  found.enabled = verb === "enable";
  if (verb === "enable") return json(200, found);
  const active = api.activeRuns[found.id] ?? [];
  return json(200, { ...found, active_runs: active.slice(0, 50), active_runs_total: active.length });
}

/** `POST /rules/{id}/stop-runs` (d17): list, or with `apply` cancel, a disabled rule's runs. */
function stopRuns(api: FakeApi, found: Rule, body: unknown): FakeResponse {
  const id = found.id;
  if (found.enabled !== false) return error(409, "rule_enabled", `rule ${id} is enabled`);
  const active = api.activeRuns[id] ?? [];
  const apply = (body as { apply?: boolean } | undefined)?.apply === true;
  if (apply) api.activeRuns[id] = [];
  return json(200, {
    rule_id: id,
    applied: apply,
    runs: active.slice(0, 50),
    total: active.length,
    cancelled: apply ? active.map((r) => r.id) : [],
  });
}

function answerAsk(api: FakeApi, id: string, body: unknown): FakeResponse {
  const found = api.asks.find((a) => a.id === id);
  if (!found) return error(404, "ask_not_found", `ask ${id} does not exist`);
  if (found.status !== "open") return error(409, "ask_already_answered", "already answered");
  found.status = "answered";
  found.answer = (body as { answer: unknown }).answer;
  api.waitingRuns = [];
  return json(200, found);
}

export function handle(
  api: FakeApi,
  method: string,
  path: string,
  query: URLSearchParams,
  body?: unknown,
): FakeResponse {
  api.calls.push({ method, path, body });
  const key = `${method} ${path}`;
  const forced = api.failNext[key];
  if (forced) {
    delete api.failNext[key];
    return error(forced.status, forced.code, forced.message);
  }
  if (method === "GET") return handleGet(api, path, query);
  if (method === "POST" && path === "/workflows") {
    const doc = body as WorkflowDef;
    if ([...api.workflows, ...api.workflowTrash].some((w) => w.id === doc.id)) {
      return error(409, "conflict", `${doc.id} exists`);
    }
    api.workflows.push(structuredClone(doc));
    return json(201, api.workflows.at(-1));
  }
  const workflow = /^\/workflows\/([^/]+)$/.exec(path);
  if (method === "DELETE" && workflow) {
    const id = decodeURIComponent(workflow[1]);
    const at = api.workflows.findIndex((w) => w.id === id);
    if (at < 0) return error(404, "not_found", path);
    if (api.rules.some((r) => r.workflow?.id === id)) {
      return error(409, "in_use", `${id} is in use`);
    }
    api.workflowTrash.push(api.workflows.splice(at, 1)[0]);
    return json(200, { id, deleted: true });
  }
  if (method === "POST" && path === "/rules") {
    const doc = body as Rule;
    if (api.rules.some((r) => r.id === doc.id)) return error(409, "conflict", `${doc.id} exists`);
    api.rules.push({ enabled: true, ...doc });
    return json(201, api.rules.at(-1));
  }
  const variable = /^\/variables\/([^/]+)$/.exec(path);
  if (method === "PUT" && variable) return variableWrite(api, decodeURIComponent(variable[1]), body);
  const rule = /^\/rules\/([^/]+)(?:\/(enable|disable|restore|stop-runs))?$/.exec(path);
  if (rule) {
    const done = handleRuleWrite(api, method, decodeURIComponent(rule[1]), rule[2], body);
    if (done) return done;
  }
  const ask = /^\/asks\/([^/]+)\/answer$/.exec(path);
  if (method === "POST" && ask) return answerAsk(api, ask[1], body);
  return error(404, "not_found", path);
}

/** A `fetch` stub over the fake API, for vitest. */
export function fetchFor(api: FakeApi): typeof fetch {
  // Not `async`: the work is synchronous. `Promise.resolve().then` still answers
  // asynchronously, like a real fetch, and turns a throw (bad JSON body) into a rejection.
  return ((input: RequestInfo | URL, init?: RequestInit) =>
    Promise.resolve().then(() => respond(api, input, init))) as typeof fetch;
}

function urlOf(input: RequestInfo | URL): string {
  if (typeof input === "string") return input;
  return input instanceof URL ? input.href : input.url;
}

function respond(api: FakeApi, input: RequestInfo | URL, init?: RequestInit): Response {
  // The base only resolves the relative `/api/...` paths the client sends.
  const url = new URL(urlOf(input), "https://localhost");
  const path = url.pathname.replace(/^\/api/, "");
  const raw = init?.body;
  const res = handle(
    api,
    (init?.method ?? "GET").toUpperCase(),
    path,
    url.searchParams,
    typeof raw === "string" ? JSON.parse(raw) : undefined,
  );
  return new Response(JSON.stringify(res.body), {
    status: res.status,
    headers: { "content-type": "application/json" },
  });
}
