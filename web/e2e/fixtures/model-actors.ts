import type { Page } from "@playwright/test";
import {
  ACTOR_IN_USE,
  ENROL_APPLIED,
  ENROL_PLAN,
  GENERIC_NOT_FOUND,
  MODEL_GATEWAYS_VARIABLE,
  REFERENCED_WORKFLOWS,
  REFERENCES,
  REVISION_CONFLICT,
} from "../../src/actors/actors-fixture";

export interface ModelCall {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

export interface ModelActorsOptions {
  /** An older server: every model-actor route is FastAPI's generic 404 (decision c33). */
  old?: boolean;
  /** The first repoint is refused as stale (409 revision_conflict). */
  staleOnce?: boolean;
}

/**
 * The model-actor routes (api/openapi.json: `/actors/enrol-models`,
 * `/actors/{id}/references`, `/workflows/{id}/repoint-actor`, `/actors/{id}/disable` with
 * its actor_in_use guard) plus `GET /variables/model_gateways` and `GET /workflows/{id}`.
 * Compose after `mockApi` and `mockActorsApi`: registered last, it answers first and hands
 * every other call back with `route.fallback()`.
 */
export async function mockModelActorsApi(page: Page, options: ModelActorsOptions = {}): Promise<ModelCall[]> {
  const calls: ModelCall[] = [];
  let references = structuredClone(REFERENCES);
  let revision = REFERENCED_WORKFLOWS.newspaper.revision;
  let stale = options.staleOnce ?? false;
  const json = (status: number, body: unknown) => ({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const p = url.pathname;
    const raw = request.postData();
    const body = raw ? JSON.parse(raw) : undefined;
    const refs = p.match(/^\/api\/actors\/([^/]+)\/references$/);
    const repoint = p.match(/^\/api\/workflows\/([^/]+)\/repoint-actor$/);
    const workflow = method === "GET" ? p.match(/^\/api\/workflows\/([^/]+)$/) : null;
    const disable = method === "POST" ? p.match(/^\/api\/actors\/([^/]+)\/disable$/) : null;
    const enrol = p === "/api/actors/enrol-models";
    const gateways = p === "/api/variables/model_gateways";
    if (!(refs || repoint || workflow || disable || enrol || gateways)) return route.fallback();
    calls.push({ method, path: p, search: url.search, body });

    if (options.old && (refs || repoint || enrol)) return route.fulfill(json(404, GENERIC_NOT_FOUND));
    if (gateways) return route.fulfill(json(200, MODEL_GATEWAYS_VARIABLE));
    if (enrol) return route.fulfill(json(200, body?.dry_run === false ? ENROL_APPLIED : ENROL_PLAN));
    if (refs) {
      return route.fulfill(
        json(200, refs[1] === REFERENCES.actor_id ? references : { actor_id: refs[1], items: [] }),
      );
    }
    if (workflow) {
      const def = REFERENCED_WORKFLOWS[decodeURIComponent(workflow[1])];
      if (!def) return route.fallback();
      return route.fulfill(json(200, { ...def, revision: def.id === "newspaper" ? revision : def.revision }));
    }
    if (repoint) {
      const id = decodeURIComponent(repoint[1]);
      if (stale) {
        // Someone saved the workflow meanwhile: the revision moved on.
        stale = false;
        revision += 1;
        return route.fulfill(json(409, REVISION_CONFLICT));
      }
      references = { ...references, items: references.items.filter((r) => r.id !== id) };
      revision += 1;
      return route.fulfill(
        json(200, {
          workflow_id: id,
          dry_run: false,
          from: body.from,
          to: body.to,
          changed: ["summarise", "edition.body[write]"],
          warnings: [],
          revision,
          digest: null,
          digest_v2: null,
        }),
      );
    }
    // disable: refused while something still uses the actor, unless forced.
    const actorId = decodeURIComponent(disable![1]);
    if (!options.old && url.searchParams.get("force") !== "true" && actorId === REFERENCES.actor_id && references.items.length) {
      return route.fulfill(json(409, ACTOR_IN_USE));
    }
    return route.fallback();
  });
  return calls;
}
