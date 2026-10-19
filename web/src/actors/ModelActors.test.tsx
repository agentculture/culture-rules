import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Actors from "../routes/Actors";
import { resetAgentState } from "../agent-state/store";
import { resetWhoamiForTests } from "../hooks/useWhoami";
import { MACHINES } from "../fixtures/rules-fixture";
import {
  ACTORS,
  ACTOR_IN_USE,
  ENROL_APPLIED,
  ENROL_PLAN,
  GENERIC_NOT_FOUND,
  MODEL_GATEWAYS_VARIABLE,
  REFERENCED_WORKFLOWS,
  REFERENCES,
  REVISION_CONFLICT,
} from "./actors-fixture";

interface Call {
  method: string;
  path: string;
  search: string;
  body: unknown;
}

type Reply = { status: number; body: unknown };

/**
 * A stateful fetch stub of the routes the Actors board calls, the model-actor
 * routes included. `overrides` answer `METHOD /path?search` exactly (`METHOD /path` for a
 * call with no query) first; a function override sees the call count for that key.
 */
function mockApi(
  options: { roles?: string[]; overrides?: Record<string, Reply | ((n: number) => Reply)> } = {},
) {
  const calls: Call[] = [];
  const counts = new Map<string, number>();
  let actors = structuredClone(ACTORS) as unknown as Record<string, unknown>[];
  let references = structuredClone(REFERENCES);
  const reply = (status: number, body: unknown) =>
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  const notFound = (path: string) => reply(404, { error: { code: "not_found", message: `${path} does not exist`, errors: [] } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(typeof input === "string" ? input : input.toString(), "http://x");
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ method, path: url.pathname, search: url.search, body });
      for (const key of [`${method} ${url.pathname}${url.search}`]) {
        const override = options.overrides?.[key];
        if (override) {
          const n = (counts.get(key) ?? 0) + 1;
          counts.set(key, n);
          const r = typeof override === "function" ? override(n) : override;
          return reply(r.status, r.body);
        }
      }
      const p = url.pathname;
      if (p === "/api/whoami") return reply(200, { identity: "ori", kind: "sso", roles: options.roles ?? ["viewer", "editor", "admin"] });
      if (p === "/api/machines") return reply(200, { items: MACHINES });
      if (p === "/api/actors" && method === "GET") return reply(200, { items: actors });
      if (p === "/api/variables/model_gateways") return reply(200, MODEL_GATEWAYS_VARIABLE);
      if (p === "/api/actors/enrol-models") return reply(200, body?.dry_run === false ? ENROL_APPLIED : ENROL_PLAN);
      const refs = p.match(/^\/api\/actors\/([^/]+)\/references$/);
      if (refs) {
        if (!actors.some((a) => a.id === refs[1])) return notFound(`actors/${refs[1]}`);
        return reply(200, refs[1] === "claude-code" ? references : { actor_id: refs[1], items: [] });
      }
      const wf = p.match(/^\/api\/workflows\/([^/]+)$/);
      if (wf) return REFERENCED_WORKFLOWS[wf[1]] ? reply(200, REFERENCED_WORKFLOWS[wf[1]]) : notFound(`workflows/${wf[1]}`);
      const repoint = p.match(/^\/api\/workflows\/([^/]+)\/repoint-actor$/);
      if (repoint) {
        references = { ...references, items: references.items.filter((r) => r.id !== repoint[1]) };
        return reply(200, {
          workflow_id: repoint[1],
          dry_run: false,
          from: body.from,
          to: body.to,
          changed: ["summarise", "edition.body[write]"],
          warnings: [],
          revision: (body.expected_revision ?? 0) + 1,
          digest: null,
          digest_v2: null,
        });
      }
      const m = p.match(/^\/api\/actors\/([^/]+)(?:\/(enable|disable))?$/);
      if (m) {
        const found = actors.find((a) => a.id === m[1]);
        if (!found) return notFound(`actors/${m[1]}`);
        if (method === "PUT") {
          Object.assign(found, body);
          return reply(200, found);
        }
        if (m[2]) {
          found.enabled = m[2] === "enable";
          return reply(200, found);
        }
      }
      if (p === "/api/actors" && method === "POST") {
        actors = [...actors, body];
        return reply(201, body);
      }
      return notFound(p);
    }),
  );
  return { calls };
}

function renderActors(path = "/actors") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/actors" element={<Actors />} />
      </Routes>
    </MemoryRouter>,
  );
}

const row = (name: string) => screen.getByRole("group", { name });

describe("Actors board: model actors (t10)", () => {
  beforeEach(() => {
    resetAgentState();
    resetWhoamiForTests();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  describe("Add models", () => {
    it("offers the model_gateways machines, plans with match and pools, then applies", async () => {
      const { calls } = mockApi();
      const user = userEvent.setup();
      renderActors();
      await user.click(await screen.findByRole("button", { name: "Add models" }));
      const panel = screen.getByRole("region", { name: "Add models" });
      // The gateways: machine names, the URL as secondary text.
      const spark = await within(panel).findByRole("checkbox", { name: /spark http:\/\/127\.0\.0\.1:4000/ });
      const spark2 = within(panel).getByRole("checkbox", { name: /spark2/ });
      expect(spark).not.toBeChecked();
      await user.click(spark);
      await user.click(spark2);
      await user.type(within(panel).getByRole("textbox", { name: "Models to match" }), "qwen*");
      await user.type(within(panel).getByRole("textbox", { name: "Pools" }), "qwen3-coder=coder");
      await user.click(within(panel).getByRole("button", { name: "Plan" }));

      const plan = await within(panel).findByRole("list", { name: "Plan" });
      const items = within(plan).getAllByRole("listitem").map((li) => li.textContent);
      expect(items).toEqual([
        expect.stringMatching(/create.*qwen3-coder.*served by spark/),
        expect.stringMatching(/create.*qwen3-vl.*served by spark2/),
        expect.stringMatching(/unchanged.*qwen3-8b/),
        expect.stringMatching(/refused.*qwen3-max.*id held by a human actor/),
      ]);
      expect(within(panel).getByText(/qwen2\.5-old/)).toBeInTheDocument();
      expect(within(panel).getByText(/qwen3-coder is also on spark2/)).toBeInTheDocument();

      const dry = calls.find((c) => c.path === "/api/actors/enrol-models");
      expect(dry?.body).toEqual({
        gateways: ["spark", "spark2"],
        match: ["qwen*"],
        pools: ["qwen3-coder=coder"],
        dry_run: true,
      });

      await user.click(within(panel).getByRole("button", { name: "Apply 2 changes" }));
      const result = await within(panel).findByRole("status");
      expect(result).toHaveTextContent("Added qwen3-coder");
      expect(within(panel).getByRole("alert")).toHaveTextContent(
        "qwen3-vl: model_taken: qwen3-vl is held by vision; disable vision first",
      );
      const applied = calls.filter((c) => c.path === "/api/actors/enrol-models");
      expect(applied.at(-1)?.body).toMatchObject({ dry_run: false, gateways: ["spark", "spark2"] });
      // The roster is read again so the new actors show.
      await waitFor(() =>
        expect(calls.filter((c) => c.method === "GET" && c.path === "/api/actors")).toHaveLength(2),
      );
    });

    it("a changed input drops the plan, so a plan is never applied for other inputs", async () => {
      mockApi();
      const user = userEvent.setup();
      renderActors();
      await user.click(await screen.findByRole("button", { name: "Add models" }));
      const panel = screen.getByRole("region", { name: "Add models" });
      await user.click(await within(panel).findByRole("checkbox", { name: /^spark\b/ }));
      await user.type(within(panel).getByRole("textbox", { name: "Models to match" }), "qwen*");
      await user.click(within(panel).getByRole("button", { name: "Plan" }));
      await within(panel).findByRole("list", { name: "Plan" });
      await user.type(within(panel).getByRole("textbox", { name: "Models to match" }), ", llama*");
      expect(within(panel).queryByRole("list", { name: "Plan" })).toBeNull();
      expect(within(panel).queryByRole("button", { name: /^Apply/ })).toBeNull();
    });

    it("needs a gateway and a match before it plans", async () => {
      const { calls } = mockApi();
      const user = userEvent.setup();
      renderActors();
      await user.click(await screen.findByRole("button", { name: "Add models" }));
      const panel = screen.getByRole("region", { name: "Add models" });
      await within(panel).findByRole("checkbox", { name: /^spark\b/ });
      expect(within(panel).getByRole("button", { name: "Plan" })).toBeDisabled();
      expect(calls.some((c) => c.path === "/api/actors/enrol-models")).toBe(false);
    });

    it("says so when model_gateways is unset, pointing to the Variables tab", async () => {
      mockApi({
        overrides: {
          "GET /api/variables/model_gateways": {
            status: 404,
            body: { error: { code: "not_found", message: "variables/model_gateways does not exist", errors: [] } },
          },
        },
      });
      const user = userEvent.setup();
      renderActors();
      await user.click(await screen.findByRole("button", { name: "Add models" }));
      const panel = screen.getByRole("region", { name: "Add models" });
      expect(await within(panel).findByText(/No model gateways yet/)).toBeInTheDocument();
      expect(within(panel).getByRole("link", { name: "Variables" })).toHaveAttribute(
        "href",
        "/variables?name=model_gateways",
      );
      expect(within(panel).queryByRole("button", { name: "Plan" })).toBeNull();
    });

    it("shows the server's refusal of a plan (e.g. gateway_unavailable)", async () => {
      mockApi({
        overrides: {
          "POST /api/actors/enrol-models": {
            status: 502,
            body: { error: { code: "gateway_unavailable", message: "gateway spark did not list its models", errors: [] } },
          },
        },
      });
      const user = userEvent.setup();
      renderActors();
      await user.click(await screen.findByRole("button", { name: "Add models" }));
      const panel = screen.getByRole("region", { name: "Add models" });
      await user.click(await within(panel).findByRole("checkbox", { name: /^spark\b/ }));
      await user.type(within(panel).getByRole("textbox", { name: "Models to match" }), "*");
      await user.click(within(panel).getByRole("button", { name: "Plan" }));
      expect(await within(panel).findByRole("alert")).toHaveTextContent("gateway spark did not list its models");
    });

    it("is offered to admins only", async () => {
      mockApi({ roles: ["viewer", "editor"] });
      renderActors();
      await screen.findByRole("region", { name: "Claude Code used by" });
      expect(screen.queryByRole("button", { name: "Add models" })).toBeNull();
    });
  });

  describe("Used by and Move", () => {
    it("lists the selected actor's references, each linking its workflow", async () => {
      mockApi();
      renderActors();
      const usedBy = await screen.findByRole("region", { name: "Claude Code used by" });
      const newspaper = await within(usedBy).findByRole("link", { name: "newspaper" });
      expect(newspaper).toHaveAttribute("href", "/workflows?id=newspaper");
      expect(within(usedBy).getByText("summarise, edition.body[write]")).toBeInTheDocument();
      expect(within(usedBy).getByRole("link", { name: "review-pr" })).toHaveAttribute("href", "/workflows?id=review-pr");
      expect(within(usedBy).getByRole("link", { name: "nightly-news" })).toHaveAttribute("href", "/rules/nightly-news");
      // Move is for a workflow's steps; a rule's placement is changed on its workflow.
      expect(within(usedBy).getAllByRole("button", { name: /^Move / })).toHaveLength(2);
    });

    it("says when nothing uses the actor", async () => {
      mockApi();
      renderActors("/actors?id=codex");
      const usedBy = await screen.findByRole("region", { name: "Codex used by" });
      expect(await within(usedBy).findByText("Nothing uses Codex.")).toBeInTheDocument();
    });

    it("moves a workflow to another actor with the revision it read", async () => {
      const { calls } = mockApi();
      const user = userEvent.setup();
      renderActors();
      const usedBy = await screen.findByRole("region", { name: "Claude Code used by" });
      await user.click(await within(usedBy).findByRole("button", { name: "Move newspaper" }));
      const form = within(usedBy).getByRole("form", { name: "Move newspaper" });
      const to = within(form).getByRole("combobox", { name: "To" });
      // Enabled actors other than this one.
      const options = within(to).getAllByRole("option").map((o) => o.getAttribute("value"));
      expect(options).toContain("codex");
      expect(options).not.toContain("claude-code");
      expect(options).not.toContain("reachy");
      await user.selectOptions(to, "codex");
      await user.click(within(form).getByRole("button", { name: "Move" }));
      expect(await within(usedBy).findByRole("status")).toHaveTextContent("Moved newspaper to Codex");
      expect(calls).toContainEqual({
        method: "POST",
        path: "/api/workflows/newspaper/repoint-actor",
        search: "",
        body: { from: "claude-code", to: "codex", expected_revision: 4, force: false, dry_run: false },
      });
      // The references are read again: newspaper no longer uses Claude Code.
      await waitFor(() => expect(within(usedBy).queryByRole("link", { name: "newspaper" })).toBeNull());
    });

    it("a stale revision says the workflow changed and offers Reload", async () => {
      const { calls } = mockApi({
        overrides: { "POST /api/workflows/newspaper/repoint-actor": (n) => (n === 1 ? { status: 409, body: REVISION_CONFLICT } : { status: 200, body: { workflow_id: "newspaper", dry_run: false, from: "claude-code", to: "codex", changed: ["summarise"], warnings: [], revision: 6, digest: null, digest_v2: null } }) },
      });
      const user = userEvent.setup();
      renderActors();
      const usedBy = await screen.findByRole("region", { name: "Claude Code used by" });
      await user.click(await within(usedBy).findByRole("button", { name: "Move newspaper" }));
      const form = within(usedBy).getByRole("form", { name: "Move newspaper" });
      await user.selectOptions(within(form).getByRole("combobox", { name: "To" }), "codex");
      await user.click(within(form).getByRole("button", { name: "Move" }));
      const alert = await within(usedBy).findByRole("alert");
      expect(alert).toHaveTextContent("This workflow changed since you opened it.");
      const reads = () => calls.filter((c) => c.method === "GET" && c.path === "/api/workflows/newspaper").length;
      const before = reads();
      await user.click(within(alert).getByRole("button", { name: "Reload" }));
      await waitFor(() => expect(reads()).toBe(before + 1));
      await waitFor(() => expect(within(usedBy).queryByRole("alert")).toBeNull());
      // The form is still open with the choice kept; a second Move goes through.
      await user.click(within(usedBy).getByRole("button", { name: "Move" }));
      expect(await within(usedBy).findByRole("status")).toHaveTextContent("Moved newspaper to Codex");
    });

    it("a trusted workflow is refused with the server's message and a deliberate Move anyway", async () => {
      const { calls } = mockApi({
        overrides: {
          "POST /api/workflows/newspaper/repoint-actor": (n) =>
            n === 1
              ? {
                  status: 409,
                  body: {
                    error: {
                      code: "workflow_trusted",
                      message: "workflows/newspaper is trusted (digest abc); a repoint changes its digest and drops that trust",
                      errors: [],
                    },
                  },
                }
              : { status: 200, body: { workflow_id: "newspaper", dry_run: false, from: "claude-code", to: "codex", changed: ["summarise"], warnings: ["repoint drops trust: abc no longer matches"], revision: 5, digest: null, digest_v2: null } },
        },
      });
      const user = userEvent.setup();
      renderActors();
      const usedBy = await screen.findByRole("region", { name: "Claude Code used by" });
      await user.click(await within(usedBy).findByRole("button", { name: "Move newspaper" }));
      await user.selectOptions(within(usedBy).getByRole("combobox", { name: "To" }), "codex");
      await user.click(within(usedBy).getByRole("button", { name: "Move" }));
      expect(await within(usedBy).findByRole("alert")).toHaveTextContent("is trusted (digest abc)");
      await user.click(within(usedBy).getByRole("button", { name: "Move anyway" }));
      const status = await within(usedBy).findByRole("status");
      expect(status).toHaveTextContent("Moved newspaper to Codex");
      expect(status).toHaveTextContent("repoint drops trust");
      expect(calls.filter((c) => c.path === "/api/workflows/newspaper/repoint-actor").at(-1)?.body).toMatchObject({ force: true });
    });

    it("viewers see Used by without Move", async () => {
      mockApi({ roles: ["viewer"] });
      renderActors();
      const usedBy = await screen.findByRole("region", { name: "Claude Code used by" });
      await within(usedBy).findByRole("link", { name: "newspaper" });
      expect(within(usedBy).queryByRole("button", { name: /^Move/ })).toBeNull();
    });
  });

  describe("Disable while in use", () => {
    it("shows the in-use refusal naming the workflows, and disables only on Disable anyway", async () => {
      const { calls } = mockApi({
        overrides: { "POST /api/actors/claude-code/disable": { status: 409, body: ACTOR_IN_USE } },
      });
      const user = userEvent.setup();
      renderActors();
      await screen.findByRole("region", { name: "Claude Code used by" });
      await user.click(within(row("Claude Code")).getByRole("switch", { name: "Claude Code enabled" }));
      const refusal = await within(row("Claude Code")).findByRole("alert");
      expect(refusal).toHaveTextContent("Claude Code is still used by");
      expect(within(refusal).getByRole("link", { name: "newspaper" })).toHaveAttribute("href", "/workflows?id=newspaper");
      expect(within(refusal).getByRole("link", { name: "review-pr" })).toHaveAttribute("href", "/workflows?id=review-pr");
      expect(within(refusal).getByRole("link", { name: "nightly-news" })).toHaveAttribute("href", "/rules/nightly-news");
      expect(within(row("Claude Code")).getByRole("switch")).toHaveAttribute("aria-checked", "true");

      await user.click(within(refusal).getByRole("button", { name: "Disable anyway" }));
      await waitFor(() =>
        expect(within(row("Claude Code")).getByRole("switch")).toHaveAttribute("aria-checked", "false"),
      );
      expect(calls).toContainEqual({ method: "POST", path: "/api/actors/claude-code/disable", search: "?force=true", body: undefined });
      expect(within(row("Claude Code")).queryByRole("alert")).toBeNull();
    });

    it("Keep enabled dismisses the refusal and sends nothing", async () => {
      const { calls } = mockApi({
        overrides: { "POST /api/actors/claude-code/disable": { status: 409, body: ACTOR_IN_USE } },
      });
      const user = userEvent.setup();
      renderActors();
      await screen.findByRole("region", { name: "Claude Code used by" });
      await user.click(within(row("Claude Code")).getByRole("switch"));
      const refusal = await within(row("Claude Code")).findByRole("alert");
      await user.click(within(refusal).getByRole("button", { name: "Keep enabled" }));
      expect(within(row("Claude Code")).queryByRole("alert")).toBeNull();
      expect(calls.some((c) => c.search === "?force=true")).toBe(false);
    });

    it("surfaces the server's model_taken message on enable", async () => {
      mockApi({
        overrides: {
          "POST /api/actors/reachy/enable": {
            status: 409,
            body: {
              error: {
                code: "model_taken",
                message: "model_taken: qwen3 is held by cortex-spark; disable cortex-spark first",
                errors: [],
              },
            },
          },
        },
      });
      const user = userEvent.setup();
      renderActors();
      await screen.findByRole("group", { name: "Reachy" });
      await user.click(within(row("Reachy")).getByRole("switch"));
      expect(await screen.findByText(/qwen3 is held by cortex-spark/)).toBeInTheDocument();
    });

    it("surfaces the server's model_taken message on save", async () => {
      mockApi({
        overrides: {
          "PUT /api/actors/claude-code": {
            status: 409,
            body: {
              error: {
                code: "model_taken",
                message: "model_taken: opus is held by codex; disable codex first",
                errors: [{ path: "params.model", code: "model_taken", message: "x" }],
              },
            },
          },
        },
      });
      const user = userEvent.setup();
      renderActors();
      const card = await screen.findByRole("group", { name: "Claude Code" });
      await user.click(within(card).getByRole("button", { name: "Edit Claude Code" }));
      await user.click(within(card).getByRole("button", { name: "Save" }));
      expect(await screen.findByText(/opus is held by codex; disable codex first/)).toBeInTheDocument();
    });
  });

  describe("an older server without the model-actor routes (c33)", () => {
    const old = {
      overrides: {
        "GET /api/actors/claude-code/references": { status: 404, body: GENERIC_NOT_FOUND },
        "GET /api/actors/codex/references": { status: 404, body: GENERIC_NOT_FOUND },
      },
    };

    it("hides Add models, Used by and Move", async () => {
      const { calls } = mockApi(old);
      renderActors();
      await screen.findByRole("group", { name: "Claude Code" });
      await waitFor(() => expect(calls.some((c) => c.path.endsWith("/references"))).toBe(true));
      // Give the probe's answer a chance to render.
      await new Promise((r) => setTimeout(r, 20));
      expect(screen.queryByRole("button", { name: "Add models" })).toBeNull();
      expect(screen.queryByRole("region", { name: /used by/ })).toBeNull();
      expect(screen.queryByRole("button", { name: /^Move/ })).toBeNull();
    });

    it("Disable falls back to plain behaviour", async () => {
      const { calls } = mockApi(old);
      const user = userEvent.setup();
      renderActors();
      await screen.findByRole("group", { name: "Claude Code" });
      await user.click(within(row("Claude Code")).getByRole("switch"));
      await waitFor(() =>
        expect(within(row("Claude Code")).getByRole("switch")).toHaveAttribute("aria-checked", "false"),
      );
      expect(calls).toContainEqual({ method: "POST", path: "/api/actors/claude-code/disable", search: "", body: undefined });
    });

    it("a real not_found envelope still counts as the routes being there", async () => {
      mockApi({
        overrides: {
          "GET /api/actors/claude-code/references": {
            status: 404,
            body: { error: { code: "not_found", message: "actors/claude-code does not exist", errors: [] } },
          },
        },
      });
      renderActors();
      expect(await screen.findByRole("button", { name: "Add models" })).toBeInTheDocument();
    });
  });
});
