import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resetActionCatalogue, type ActionCatalogue, type ActionKindSpec } from "../api/actions";
import type { Actor } from "../api/actors";
import type { Action } from "../api/types";
import { ACTION_CATALOGUE } from "../fixtures/action-catalogue";
import ActionPicker, { actionProblem, actorsFor, blankAction } from "./ActionPicker";

/* The picker is built from the API's catalogue: a kind an extension registers needs no editor change. */

const param = (name: string, extra: Partial<ActionKindSpec["params"][number]> = {}) => ({
  name,
  type: "str",
  required: false,
  label: "",
  widget: "",
  choices: [],
  options_from: "",
  choose: "",
  needs_actor_param: "",
  needed: false,
  advanced: false,
  ...extra,
});

const ACME: ActionKindSpec = {
  kind: "acme.ping",
  label: "Ping Acme",
  summary: "Ping Acme through its service",
  lenient: false,
  aliases: ["acme.old"],
  params: [
    param("actor", { required: true }),
    param("words", { required: true, label: "Words to send", widget: "multiline" }),
    param("trace_id", { advanced: true, label: "Trace id" }),
    param("speed", { choices: [{ value: "fast", label: "Fast lane" }, { value: "slow", label: "Slow lane" }], choose: "Any speed" }),
  ],
  any_of: [],
  actors: [{ kinds: ["service"], has: ["base_url"] }],
  actorless: false,
};

const WITH_ACME: ActionCatalogue = { items: [...ACTION_CATALOGUE.items, ACME] };
const ACME_ACTOR: Actor = {
  id: "acme-service",
  name: "Acme service",
  kind: "service",
  enabled: true,
  params: { base_url: "http://acme.example" },
};

function Harness({ start, catalogue }: Readonly<{ start?: Action; catalogue: ActionCatalogue }>) {
  const [value, setValue] = useState<Action>(start ?? blankAction());
  return (
    <>
      <ActionPicker value={value} actors={[ACME_ACTOR]} catalogue={catalogue} onChange={setValue} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Action;
const offeredKinds = () =>
  within(screen.getByLabelText("What happens")).getAllByRole("option").map((o) => o.getAttribute("value"));

describe("a kind the editor has never heard of", () => {
  it("is offered when the catalogue holds it and an actor can do it", () => {
    render(<Harness catalogue={WITH_ACME} />);
    expect(offeredKinds()).toContain("acme.ping");
  });

  it("is named by its served label", () => {
    render(<Harness catalogue={WITH_ACME} />);
    expect(screen.getByRole("option", { name: "Ping Acme" })).toBeDefined();
  });

  it("is not offered while the catalogue lacks it", () => {
    render(<Harness catalogue={ACTION_CATALOGUE} />);
    expect(offeredKinds()).not.toContain("acme.ping");
  });

  it("lists only the actors its clauses match", async () => {
    const user = userEvent.setup();
    render(<Harness catalogue={WITH_ACME} />);
    await user.selectOptions(screen.getByLabelText("What happens"), "acme.ping");
    const options = within(screen.getByLabelText("Actor")).getAllByRole("option");
    expect(options.map((o) => o.getAttribute("value"))).toEqual(["", "acme-service"]);
  });

  it("renders its param under the served label and saves what is typed", async () => {
    const user = userEvent.setup();
    render(<Harness catalogue={WITH_ACME} start={{ kind: "acme.ping", params: {} }} />);
    await user.type(screen.getByLabelText("Words to send"), "hello");
    expect(saved()).toEqual({ kind: "acme.ping", params: { words: "hello" } });
  });

  it("renders a param with choices as a select that opens on the served prompt", () => {
    render(<Harness catalogue={WITH_ACME} start={{ kind: "acme.ping", params: {} }} />);
    const select = screen.getByLabelText("Speed");
    expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["Any speed", "Fast lane", "Slow lane"]);
  });

  it("asks for its required param before it can be saved", () => {
    const action = { kind: "acme.ping", params: { actor: "acme-service" } };
    expect(actionProblem(action, WITH_ACME)).toBe("empty");
  });

  it("is complete once its required param is set", () => {
    const action = { kind: "acme.ping", params: { actor: "acme-service", words: "hi" } };
    expect(actionProblem(action, WITH_ACME)).toBeNull();
  });

  it("refuses a value outside its choices", () => {
    const action = { kind: "acme.ping", params: { actor: "acme-service", words: "hi", speed: "warp" } };
    expect(actionProblem(action, WITH_ACME)).toBe("invalid_value");
  });

  it("is found by its legacy alias", () => {
    const action = { kind: "acme.old", params: { actor: "acme-service" } };
    expect(actionProblem(action, WITH_ACME)).toBe("empty");
  });

  it("has actors by clause, none for a kind outside the catalogue", () => {
    expect(actorsFor("acme.ping", [ACME_ACTOR], WITH_ACME).map((a) => a.id)).toEqual(["acme-service"]);
    expect(actorsFor("acme.ping", [ACME_ACTOR], ACTION_CATALOGUE)).toEqual([]);
  });
});

describe("a stored kind the catalogue does not hold", () => {
  it("is kept as it is", () => {
    render(<Harness catalogue={ACTION_CATALOGUE} start={{ kind: "acme.ping", params: { words: "x" } }} />);
    expect(screen.getByText(/the editor cannot change/)).toBeDefined();
  });

  it("is never a problem to save", () => {
    expect(actionProblem({ kind: "acme.ping", params: {} }, ACTION_CATALOGUE)).toBeNull();
  });
});

describe("the catalogue from the API", () => {
  afterEach(() => {
    asked.length = 0;
    vi.unstubAllGlobals();
    resetActionCatalogue();
  });

  const asked: string[] = [];
  const answer = (body: unknown) => (input: RequestInfo | URL) => {
    asked.push(String(input));
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  };

  function Fetched() {
    const [value, setValue] = useState<Action>(blankAction());
    return <ActionPicker value={value} actors={[ACME_ACTOR]} onChange={setValue} />;
  }

  it("is fetched from /actions/kinds and offers a kind it holds that the editor lacks", async () => {
    const fetcher = answer(WITH_ACME);
    vi.stubGlobal("fetch", fetcher);
    resetActionCatalogue();
    render(<Fetched />);
    expect(screen.getByText("Loading the kinds of action…")).toBeDefined();
    await screen.findByLabelText("What happens");
    expect(offeredKinds()).toContain("acme.ping");
    expect(asked).toEqual(["/api/actions/kinds"]);
  });

  it("says so when it cannot be loaded, and loads on Try again", async () => {
    const user = userEvent.setup();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 500 }))
      .mockResolvedValue(new Response(JSON.stringify(WITH_ACME), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    resetActionCatalogue();
    render(<Fetched />);
    await screen.findByText("Could not load the kinds of action the server offers.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    await screen.findByLabelText("What happens");
    expect(offeredKinds()).toContain("acme.ping");
  });
});

describe("advanced params", () => {
  const start = (params: Record<string, unknown>): Action => ({ kind: "acme.ping", params });

  it("sit behind a collapsed More settings by default", () => {
    render(<Harness catalogue={WITH_ACME} start={start({})} />);
    expect(screen.getByText("More settings").closest("details")?.open).toBe(false);
  });

  it("are inside the fold, not among the always-shown fields", () => {
    render(<Harness catalogue={WITH_ACME} start={start({})} />);
    expect(screen.getByLabelText("Trace id", { selector: "input" }).closest("details")).not.toBeNull();
  });

  it("open the fold on a click", async () => {
    const user = userEvent.setup();
    render(<Harness catalogue={WITH_ACME} start={start({})} />);
    await user.click(screen.getByText("More settings"));
    expect(screen.getByText("More settings").closest("details")?.open).toBe(true);
  });

  it("show expanded when the stored action already sets one", () => {
    render(<Harness catalogue={WITH_ACME} start={start({ trace_id: "t-1" })} />);
    expect(screen.getByText("More settings").closest("details")?.open).toBe(true);
  });

  it("keep the field's value when shown", () => {
    render(<Harness catalogue={WITH_ACME} start={start({ trace_id: "t-1" })} />);
    expect(screen.getByLabelText("Trace id", { selector: "input" })).toHaveValue("t-1");
  });

  it("are absent for a kind that has none", () => {
    render(<Harness catalogue={ACTION_CATALOGUE} start={{ kind: "github.pr", params: {} }} />);
    expect(screen.queryByText("More settings")).toBeNull();
  });

  it("fold the built-in once_key of github.comment, as the old editor hid it", () => {
    const actors: Actor[] = [{ id: "g", name: "G", kind: "app", enabled: true, params: { surface: "github", actions: ["github.comment"] } }];
    render(<ActionPicker value={{ kind: "github.comment", params: {} }} actors={actors} catalogue={ACTION_CATALOGUE} onChange={() => {}} />);
    expect(screen.getByLabelText("Once key", { selector: "input" }).closest("details")?.open).toBe(false);
  });
});
