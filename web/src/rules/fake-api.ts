import type { Rule, RunSummary } from "../api/types";
import { MACHINES, RULES, WHOAMI, WORKFLOWS, runsFor } from "../fixtures/rules-fixture";

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

export interface FakeApi {
  rules: Rule[];
  trash: Rule[];
  asks: FakeAsk[];
  waitingRuns: RunSummary[];
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
    trash: [],
    asks: [],
    waitingRuns: [],
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

const json = (status: number, body: unknown): FakeResponse => ({ status, body });
const error = (status: number, code: string, message: string) =>
  json(status, { error: { code, message, errors: [] } });

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
  if (method === "GET") {
    if (path === "/whoami") return json(200, WHOAMI);
    if (path === "/rules") return json(200, { items: api.rules });
    if (path === "/machines") return json(200, { items: MACHINES });
    if (path === "/workflows") return json(200, { items: WORKFLOWS });
    if (path === "/runs") {
      const status = query.get("status");
      if (status === "waiting") return json(200, { items: api.waitingRuns });
      const all = [...api.waitingRuns, ...runsFor(api.now)];
      const rule = query.get("rule_id");
      return json(200, { items: all.filter((r) => !rule || r.rule_id === rule) });
    }
    if (path === "/asks") {
      const run = query.get("run_id");
      return json(200, {
        items: api.asks.filter((a) => a.status === "open" && (!run || a.run_id === run)),
      });
    }
    return error(404, "not_found", path);
  }
  const rule = path.match(/^\/rules\/([^/]+)(?:\/(enable|disable|restore))?$/);
  if (method === "POST" && path === "/rules") {
    const doc = body as Rule;
    if (api.rules.some((r) => r.id === doc.id)) return error(409, "conflict", `${doc.id} exists`);
    api.rules.push({ enabled: true, ...doc });
    return json(201, api.rules[api.rules.length - 1]);
  }
  if (rule) {
    const [, id, verb] = rule;
    if (method === "POST" && verb === "restore") {
      const at = api.trash.findIndex((r) => r.id === id);
      if (at < 0) return error(404, "not_found", id);
      api.rules.push(api.trash.splice(at, 1)[0]);
      return json(200, api.rules[api.rules.length - 1]);
    }
    const found = api.rules.find((r) => r.id === id);
    if (!found) return error(404, "not_found", `rule ${id!} does not exist`);
    if (method === "POST" && (verb === "enable" || verb === "disable")) {
      found.enabled = verb === "enable";
      return json(200, found);
    }
    if (method === "PUT") {
      Object.assign(found, body as Rule, { id });
      return json(200, found);
    }
    if (method === "DELETE") {
      api.rules.splice(api.rules.indexOf(found), 1);
      api.trash.push(found);
      return json(200, { id, deleted: true });
    }
  }
  const ask = path.match(/^\/asks\/([^/]+)\/answer$/);
  if (method === "POST" && ask) {
    const found = api.asks.find((a) => a.id === ask[1]);
    if (!found) return error(404, "ask_not_found", `ask ${ask[1]} does not exist`);
    if (found.status !== "open") return error(409, "ask_already_answered", "already answered");
    found.status = "answered";
    found.answer = (body as { answer: unknown }).answer;
    api.waitingRuns = [];
    return json(200, found);
  }
  return error(404, "not_found", path);
}

/** A `fetch` stub over the fake API, for vitest. */
export function fetchFor(api: FakeApi): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(typeof input === "string" ? input : input.toString(), "http://x");
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
  }) as typeof fetch;
}
