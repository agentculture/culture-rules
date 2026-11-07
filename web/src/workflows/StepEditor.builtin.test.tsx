import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resetBuiltinCatalogue, type BuiltinCatalogue, type BuiltinSpec } from "../api/builtins";
import type { Step, WorkflowDef } from "../api/workflows";
import { BUILTIN_CATALOGUE, builtinSpec } from "../fixtures/builtin-catalogue";
import { StepEditor } from "./StepEditor";

/* A code step's Built-in picker is built from the builtin catalogue (GET /steps/builtins): its
 * config and its typed ports, with no table in the editor. */

/** `builtins: null`: the editor fetches the catalogue itself. */
function setup(step: Step, builtins: BuiltinCatalogue | null = BUILTIN_CATALOGUE) {
  const changes: WorkflowDef[] = [];
  function Host() {
    const [wf, setWf] = useState<WorkflowDef>({ id: "wf", name: "WF", steps: [step], edges: [] });
    return (
      <StepEditor
        workflow={wf}
        stepId={step.id}
        builtins={builtins ?? undefined}
        onChange={(n) => {
          changes.push(n);
          setWf(n);
        }}
        onClose={() => {}}
      />
    );
  }
  render(<Host />);
  return () => changes[changes.length - 1].steps![0];
}

const offered = () =>
  Array.from((screen.getByLabelText("Built-in") as HTMLSelectElement).options).map((o) => o.value);

describe("markdown.render built-in", () => {
  it("is offered only on a code step", () => {
    setup({ id: "s", kind: "actor_task" });
    expect(screen.queryByLabelText("Built-in")).not.toBeInTheDocument();
  });

  it("sets config.builtin and the title/text ports (author, source, url, id optional)", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "render", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "markdown.render");
    const s = last();
    expect(s.config).toEqual({ builtin: "markdown.render" });
    expect(s.inputs?.map((p) => [p.name, p.required !== false])).toEqual([
      ["title", true],
      ["text", true],
      ["author", false],
      ["source", false],
      ["url", false],
      ["id", false],
    ]);
    expect(s.outputs?.map((p) => p.name)).toEqual(["markdown", "content_base64"]);
  });

  it("keeps existing ports and other config, and None removes only the built-in", async () => {
    const user = userEvent.setup();
    const last = setup({
      id: "render",
      kind: "code",
      config: { builtin: "markdown.render", title: "Fixed" },
      inputs: [{ name: "text", type: "string" }],
    });
    expect(screen.getByLabelText("Built-in")).toHaveValue("markdown.render");
    await user.selectOptions(screen.getByLabelText("Built-in"), "");
    expect(last().config).toEqual({ title: "Fixed" });
    expect(last().inputs).toEqual([{ name: "text", type: "string" }]);
  });
});

describe("attachment.fetch built-in", () => {
  it("sets config.builtin and its typed ports: attachments in, items out (files, d7)", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "fetch", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "attachment.fetch");
    const s = last();
    expect(s.config).toEqual({ builtin: "attachment.fetch" });
    expect(s.inputs?.map((p) => [p.name, p.type, p.required !== false])).toEqual([
      ["attachments", "array", true],
      ["output_root", "string", false],
      ["retention_days", "integer", false],
    ]);
    expect(s.outputs?.map((p) => p.name)).toEqual(["items", "fetched", "skipped"]);
  });
});

describe("cursor.advance built-in", () => {
  it("sets its ports: key, expected (optional) and value in; advanced out", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "advance", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "cursor.advance");
    const s = last();
    expect(s.config).toEqual({ builtin: "cursor.advance" });
    expect(s.inputs?.map((p) => [p.name, p.required !== false])).toEqual([
      ["key", true],
      ["expected", false],
      ["value", true],
    ]);
    expect(s.outputs?.map((p) => p.name)).toEqual(["key", "value", "previous", "advanced"]);
  });
});

describe("document.sections built-in", () => {
  it("sets config.builtin", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "split", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "document.sections");
    expect(last().config).toEqual({ builtin: "document.sections" });
  });

  it("gives every source an optional input port", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "split", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "document.sections");
    expect(last().inputs?.map((p) => [p.name, p.required !== false])).toEqual([
      ["content_base64", false],
      ["html", false],
      ["markdown", false],
      ["content_path", false],
      ["content_root", false],
      ["format", false],
    ]);
  });

  it("gives the sections, count and found outputs", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "split", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "document.sections");
    expect(last().outputs?.map((p) => p.name)).toEqual(["sections", "count", "found"]);
  });
});

describe("keyset built-ins", () => {
  it("sets config.builtin for keyset.filter", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "fresh", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "keyset.filter");
    expect(last().config).toEqual({ builtin: "keyset.filter" });
  });

  it("gives keyset.filter the items, kept, dropped and keyless outputs", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "fresh", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "keyset.filter");
    expect(last().outputs?.map((p) => p.name)).toEqual(["items", "kept", "dropped", "keyless"]);
  });

  it("gives keyset.add the added, already_present and keyless outputs", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "remember", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), "keyset.add");
    expect(last().outputs?.map((p) => p.name)).toEqual(["added", "already_present", "keyless"]);
  });
});

describe("text built-ins", () => {
  it.each([
    ["text.number", ["text", "count"]],
    ["text.replace_refs", ["text", "replaced", "missing_refs"]],
    ["text.validate", ["ok", "reasons", "text", "links"]],
  ])("offers %s with its outputs", async (name, outputs) => {
    const user = userEvent.setup();
    const last = setup({ id: "t", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), name);
    expect(last().config).toEqual({ builtin: name });
    expect(last().outputs?.map((p) => p.name)).toEqual(outputs);
  });

  it("needs text.validate's text and no rule", () => {
    expect(builtinSpec("text.validate").inputs.filter((p) => p.required).map((p) => p.name)).toEqual(["text"]);
  });
});

describe("markdown.from_html built-in", () => {
  it("declares an optional split_at input and the parts output", () => {
    const b = builtinSpec("markdown.from_html");
    expect(b.inputs.find((p) => p.name === "split_at")).toMatchObject({ required: false });
    expect(b.outputs.find((p) => p.name === "parts")).toMatchObject({ type: "array" });
    expect(b.outputs.map((p) => p.name)).toEqual(expect.arrayContaining(["markdown", "chunks", "count"]));
  });
});

describe("the building blocks", () => {
  it.each([
    ["json.parse", ["text", "shape"], ["value"]],
    ["text.join", ["items", "separator"], ["text", "count"]],
    ["time.add", ["instant", "seconds", "minutes", "hours", "days"], ["instant"]],
    ["date.local", ["instant", "tz", "format"], ["text", "local"]],
    ["file.read", ["path"], ["bytes", "content", "value", "fields"]],
    [
      "list.filter",
      ["items", "limit"],
      ["items", "count", "dropped_host", "dropped_duplicate", "dropped_where", "dropped_limit"],
    ],
  ])("offers %s with its ports", async (name, inputs, outputs) => {
    const user = userEvent.setup();
    const last = setup({ id: "b", kind: "code" });
    await user.selectOptions(screen.getByLabelText("Built-in"), name);
    expect(last().config).toEqual({ builtin: name });
    expect(last().inputs?.map((p) => p.name)).toEqual(inputs);
    expect(last().outputs?.map((p) => p.name)).toEqual(outputs);
  });

  it("labels each by its describe phrase", () => {
    setup({ id: "b", kind: "code" });
    expect(screen.getByRole("option", { name: /^list\.filter: / })).toBeInTheDocument();
  });
});

describe("the catalogue", () => {
  const ACME: BuiltinSpec = {
    name: "acme.tally",
    label: "acme.tally: count the things",
    inputs: [{ name: "things", type: "array", required: true }],
    outputs: [{ name: "count", type: "integer", required: true }],
  };
  const WITH_ACME: BuiltinCatalogue = { items: [...BUILTIN_CATALOGUE.items, ACME] };

  it("offers a builtin an extension registers, with its ports, and no editor change", async () => {
    const user = userEvent.setup();
    const last = setup({ id: "t", kind: "code" }, WITH_ACME);
    await user.selectOptions(screen.getByLabelText("Built-in"), "acme.tally");
    expect(last().inputs).toEqual(ACME.inputs);
    expect(last().outputs).toEqual(ACME.outputs);
  });

  it("offers every builtin it holds, in its order", () => {
    setup({ id: "t", kind: "code" });
    expect(offered()).toEqual(["", ...BUILTIN_CATALOGUE.items.map((b) => b.name)]);
  });

  it("keeps a stored builtin it does not hold", () => {
    setup({ id: "t", kind: "code", config: { builtin: "gone.step" } });
    expect(screen.getByLabelText("Built-in")).toHaveValue("gone.step");
  });

  it("leaves the ports alone for a builtin that declares none", async () => {
    const user = userEvent.setup();
    const bare: BuiltinCatalogue = { items: [{ name: "bare.step", label: "bare.step", inputs: [], outputs: [] }] };
    const last = setup({ id: "t", kind: "code", outputs: [{ name: "kept", type: "any" }] }, bare);
    await user.selectOptions(screen.getByLabelText("Built-in"), "bare.step");
    expect(last().outputs).toEqual([{ name: "kept", type: "any" }]);
  });
});

describe("the fetched catalogue", () => {
  afterEach(() => {
    resetBuiltinCatalogue();
    vi.unstubAllGlobals();
  });

  const asked: string[] = [];
  const answer = (body: unknown) => (input: RequestInfo | URL) => {
    asked.push(String(input));
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  };

  it("is fetched from /steps/builtins, loading until it is there", async () => {
    vi.stubGlobal("fetch", answer(BUILTIN_CATALOGUE));
    resetBuiltinCatalogue();
    setup({ id: "t", kind: "code" }, null);
    expect(screen.getByText("Loading the built-ins…")).toBeInTheDocument();
    await screen.findByLabelText("Built-in");
    expect(asked).toEqual(["/api/steps/builtins"]);
  });

  it("says so when it cannot be loaded, and loads on Try again", async () => {
    const user = userEvent.setup();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response("{}", { status: 500 }))
      .mockResolvedValue(new Response(JSON.stringify(BUILTIN_CATALOGUE), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    resetBuiltinCatalogue();
    setup({ id: "t", kind: "code" }, null);
    await screen.findByText("Could not load the built-ins the server offers.");
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByLabelText("Built-in")).toBeInTheDocument();
  });
});
