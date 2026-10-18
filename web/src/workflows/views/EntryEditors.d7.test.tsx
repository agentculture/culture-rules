import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resetAgentState } from "../../agent-state/store";
import type { Rule } from "../../api/types";
import { fetchFor, type FakeApi } from "../../rules/fake-api";
import { D7Offer } from "../simple/D7Offer";
import { NewRule } from "../simple/NewRule";
import { FOLD_RULES } from "../simple/fixture";
import {
  failure, foldApi, sent, use as useFake,
  writes,
} from "./entryHarness";

let api: FakeApi;
function use(fake: FakeApi) {
  api = fake;
  useFake(fake);
}

/**
 * D7 is offered on a rule with no workflow. Detailed does not show it (the page does: the list's
 * "Rules without a workflow", workflows/list), so it is tested on the component the page mounts.
 */
function renderOffer(onCreated?: (id: string) => void) {
  const rule = api.rules.find((r) => r.id === "lone-alert")!;
  return render(
    <MemoryRouter>
      <D7Offer rule={rule} takenIds={api.workflows.map((w) => w.id)} hrefFor={(id) => `/workflows?id=${id}`} onCreated={onCreated} />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  resetAgentState();
  use(foldApi());
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("D7: a rule without a workflow", () => {
  it("offers to create its workflow, then points the rule at it", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    renderOffer(onCreated);
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    expect(within(offer).getByLabelText("Workflow id")).toHaveValue("lone-alert");
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("lone-alert"));
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["POST /workflows", "PUT /rules/lone-alert"]);
    expect(writes()[0].body).toEqual({ id: "lone-alert", name: "Lone alert", steps: [], edges: [] });
    expect(writes()[1].body).toEqual({ ...FOLD_RULES[7], workflow: { id: "lone-alert" } });
    expect(await within(offer).findByText(/Lone alert now starts/)).toBeInTheDocument();
  });

  it("a failed attach deletes the new workflow again and says so, with a retry", async () => {
    api.failNext["PUT /rules/lone-alert"] = { status: 422, code: "invalid_rule", message: "refused" };
    const user = userEvent.setup();
    renderOffer();
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    expect(await within(offer).findByText(/was deleted again/)).toBeInTheDocument();
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual([
      "POST /workflows", "PUT /rules/lone-alert", "DELETE /workflows/lone-alert",
    ]);
    expect(within(offer).getByText(/refused/)).toBeInTheDocument();
    expect(within(offer).getByRole("button", { name: "Create its workflow" })).toBeEnabled();
  });

  it("an orphan wrapper is flagged with a fix action", async () => {
    api.failNext["PUT /rules/lone-alert"] = { status: 422, code: "invalid_rule", message: "refused" };
    api.failNext["DELETE /workflows/lone-alert"] = failure;
    const user = userEvent.setup();
    renderOffer();
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    expect(await within(offer).findByRole("alert")).toHaveTextContent(/lone-alert was created but Lone alert does not use it/);
    await user.click(within(offer).getByRole("button", { name: "Attach Lone alert to lone-alert" }));
    await waitFor(() => expect(sent("PUT", "/rules/lone-alert")).toHaveLength(2));
    expect(await within(offer).findByText(/Lone alert now starts/)).toBeInTheDocument();
  });
});

describe("review follow-up: D7 when the attach may have committed", () => {
  /** The PUT commits but its answer is lost (503), and the reconciliation re-read (the third GET: check, check-before-write, reconcile) fails. */
  function lostAttach() {
    const base = fetchFor(api);
    let reads = 0;
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (method === "PUT" && url.endsWith("/rules/lone-alert")) {
        await base(input, init);
        return new Response(JSON.stringify({ error: { code: "bad_gateway", message: "lost", errors: [] } }), { status: 503 });
      }
      if (method === "GET" && url.endsWith("/rules/lone-alert") && ++reads === 3) {
        return new Response(JSON.stringify({ error: { code: "store_down", message: "down", errors: [] } }), { status: 503 });
      }
      return base(input, init);
    }) as typeof fetch);
  }

  it("re-reads the rule before calling it an orphan, and shows it attached when it is", async () => {
    lostAttach();
    const onCreated = vi.fn();
    const user = userEvent.setup();
    renderOffer(onCreated);
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    expect(await within(offer).findByText(/Lone alert now starts/)).toBeInTheDocument();
    expect(onCreated).toHaveBeenCalledWith("lone-alert");
    expect(sent("DELETE", "/workflows/lone-alert")).toHaveLength(0);
  });

  it("Attach re-reads the rule and writes from what is stored now", async () => {
    api.failNext["PUT /rules/lone-alert"] = { status: 422, code: "invalid_rule", message: "refused" };
    api.failNext["DELETE /workflows/lone-alert"] = failure;
    const user = userEvent.setup();
    renderOffer();
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    await within(offer).findByRole("alert");
    // someone renames the rule meanwhile: the attach starts from that version, not the old snapshot
    Object.assign(api.rules.find((r) => r.id === "lone-alert")!, { name: "Lone alert (renamed)", updated_at: "2026-10-09T13:00:00Z" });
    await user.click(within(offer).getByRole("button", { name: "Attach Lone alert to lone-alert" }));
    expect(await within(offer).findByText(/now starts/)).toBeInTheDocument();
    const last = sent("PUT", "/rules/lone-alert").at(-1)!.body as Rule;
    expect(last).toMatchObject({ name: "Lone alert (renamed)", workflow: { id: "lone-alert" } });
  });
});

describe("NewRule: creating rules once the Rules tab is gone", () => {
  it("New rule creates the rule, then gives it a stepless workflow at once (D7) and reports its id", async () => {
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCreated={onCreated} onCancel={vi.fn()} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await waitFor(() => expect(sent("GET", "/workflows")).toHaveLength(1));
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("push-seen"));
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["POST /rules", "POST /workflows", "PUT /rules/push-seen"]);
    expect(writes()[1].body).toEqual({ id: "push-seen", name: "Push seen", steps: [], edges: [] });
    expect(api.rules.find((r) => r.id === "push-seen")?.workflow).toEqual({ id: "push-seen" });
    expect(await screen.findByText(/Push seen now starts Push seen/)).toBeInTheDocument();
  });

  it("New rule keeps the rule and offers the fix when the automatic D7 leaves an orphan", async () => {
    api.failNext["PUT /rules/push-seen"] = { status: 422, code: "invalid_rule", message: "refused" };
    api.failNext["DELETE /workflows/push-seen"] = failure;
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCreated={onCreated} onCancel={vi.fn()} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("push-seen was created but Push seen does not use it");
    expect(onCreated).not.toHaveBeenCalled();
    expect(api.rules.some((r) => r.id === "push-seen")).toBe(true);
    await user.click(screen.getByRole("button", { name: "Attach Push seen to push-seen" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("push-seen"));
  });
});

describe("NewRule: re-review follow-up", () => {
  it("New rule runs create-then-D7 once: no second submit, no cancel while it is in flight", async () => {
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const base = fetchFor(api);
    vi.stubGlobal("fetch", (async (input: RequestInfo | URL, init?: RequestInit) => {
      if ((init?.method ?? "GET").toUpperCase() === "POST" && String(input).endsWith("/rules")) await gate;
      return base(input, init);
    }) as typeof fetch);
    const onCreated = vi.fn();
    const onCancel = vi.fn();
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCreated={onCreated} onCancel={onCancel} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await waitFor(() => expect(sent("GET", "/workflows")).toHaveLength(1));
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    const create = within(form).getByRole("button", { name: "Create rule" });
    await user.click(create);
    await waitFor(() => expect(create).toBeDisabled());
    expect(within(form).getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(within(form).getByLabelText("Name")).toBeDisabled();
    await user.click(create);
    await user.keyboard("{Escape}");
    expect(onCancel).not.toHaveBeenCalled();
    release();
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith("push-seen"));
    expect(writes().map((c) => `${c.method} ${c.path}`)).toEqual(["POST /rules", "POST /workflows", "PUT /rules/push-seen"]);
  });

});

describe("NewRule: full-pass review follow-up", () => {
  it("New rule moves focus to the error when the create is refused", async () => {
    api.failNext["POST /rules"] = { status: 422, code: "invalid_rule", message: "refused" };
    const user = userEvent.setup();
    render(<MemoryRouter><NewRule onCancel={vi.fn()} /></MemoryRouter>);
    const form = await screen.findByRole("form", { name: "New rule" });
    await user.type(within(form).getByLabelText("Name"), "Push seen");
    await user.selectOptions(within(form).getByLabelText("Surface"), "github-app");
    await user.selectOptions(within(form).getByLabelText("Event"), "github.push");
    await user.click(within(form).getByRole("button", { name: "Create rule" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("refused");
    await waitFor(() => expect(alert).toHaveFocus());
  });
});

describe("fold-fixA: D7 never deletes a workflow something uses", () => {
  it("a manual orphan Delete keeps a wrapper another rule has adopted", async () => {
    api.failNext["PUT /rules/lone-alert"] = { status: 422, code: "invalid_rule", message: "refused" };
    api.failNext["DELETE /workflows/lone-alert"] = failure;
    const user = userEvent.setup();
    renderOffer();
    const offer = await screen.findByRole("region", { name: "Lone alert has no workflow" });
    await user.click(within(offer).getByRole("button", { name: "Create its workflow" }));
    await within(offer).findByRole("alert");
    api.rules.push({ ...structuredClone(FOLD_RULES[7]), id: "adopter", name: "Adopter", workflow: { id: "lone-alert" } });
    const deletes = sent("DELETE", "/workflows/lone-alert").length;
    await user.click(within(offer).getByRole("button", { name: "Delete lone-alert" }));
    expect(await within(offer).findByText(/lone-alert was kept: Adopter uses it/)).toBeInTheDocument();
    expect(sent("DELETE", "/workflows/lone-alert")).toHaveLength(deletes);
  });
});
