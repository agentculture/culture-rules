import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RunDoc, WorkflowDef } from "../api/workflows";
import RunForm, { RunOutputs, parseField } from "./RunForm";

const WF: WorkflowDef = {
  id: "demo",
  name: "Demo",
  enabled: true,
  inputs: [
    { name: "title", type: "string", required: true },
    { name: "count", type: "integer", required: true },
    { name: "ratio", type: "number", required: false },
    { name: "dry", type: "boolean", required: false },
    { name: "meta", type: "object", required: false },
    { name: "tags", type: "array", required: false },
    { name: "free", type: "any", required: false },
  ],
  variables: [{ name: "ratio", type: "number", default: 0.5 }],
};

const STARTED: RunDoc = { id: "run-9", status: "running", steps: [] };

type Reply = { status: number; body: unknown };
function fakeApi(reply: Reply = { status: 201, body: STARTED }) {
  const fetchMock = vi.fn(
    async () =>
      new Response(JSON.stringify(reply.body), {
        status: reply.status,
        headers: { "content-type": "application/json" },
      }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}
const sent = (f: ReturnType<typeof fakeApi>) =>
  JSON.parse((f.mock.calls[0] as unknown as [string, RequestInit])[1].body as string);

function open(wf: WorkflowDef = WF) {
  const onStarted = vi.fn();
  const onClose = vi.fn();
  render(<RunForm workflow={wf} onStarted={onStarted} onClose={onClose} />);
  return { onStarted, onClose };
}

afterEach(() => vi.unstubAllGlobals());

describe("parseField", () => {
  it("rejects a decimal for an integer and accepts it for a number", () => {
    expect(parseField("integer", "1.5").ok).toBe(false);
    expect(parseField("integer", "4")).toEqual({ ok: true, value: 4 });
    expect(parseField("number", "1.5")).toEqual({ ok: true, value: 1.5 });
  });
  it("parses object/array JSON and checks the shape", () => {
    expect(parseField("object", '{"a":1}')).toEqual({ ok: true, value: { a: 1 } });
    expect(parseField("object", "[1]").ok).toBe(false);
    expect(parseField("array", "[1,2]")).toEqual({ ok: true, value: [1, 2] });
    expect(parseField("array", "nope").ok).toBe(false);
  });
  it("any takes JSON or falls back to text", () => {
    expect(parseField("any", "12")).toEqual({ ok: true, value: 12 });
    expect(parseField("any", "hello")).toEqual({ ok: true, value: "hello" });
  });
});

describe("RunForm", () => {
  it("renders a control per declared input, marks required ones, fills model defaults, focuses the first", () => {
    open();
    expect(screen.getByRole("dialog", { name: "Run Demo" })).toBeInTheDocument();
    expect(screen.getByLabelText(/^title/)).toHaveFocus();
    expect(screen.getByLabelText(/^count/)).toHaveAttribute("type", "number");
    expect(screen.getByLabelText(/^dry/)).toHaveAttribute("type", "checkbox");
    expect(screen.getByLabelText(/^meta/).tagName).toBe("TEXTAREA");
    expect(screen.getByLabelText(/^tags/).tagName).toBe("TEXTAREA");
    expect(screen.getByLabelText(/^title/)).toBeRequired();
    expect(screen.getByLabelText(/^ratio/)).not.toBeRequired();
    expect(screen.getByText("title", { selector: "label span" }).parentElement).toHaveTextContent("title *");
    // the only default the model provides: a same-named variable's default
    expect(screen.getByLabelText(/^ratio/)).toHaveValue(0.5);
  });

  it("sends typed JSON values: number, boolean, parsed object", async () => {
    const f = fakeApi();
    const user = userEvent.setup();
    const { onStarted } = open();
    await user.type(screen.getByLabelText(/^title/), "Hello");
    await user.type(screen.getByLabelText(/^count/), "3");
    await user.click(screen.getByLabelText(/^dry/));
    await user.click(screen.getByLabelText(/^meta/));
    await user.paste('{"k": [1, 2]}');
    await user.click(screen.getByRole("button", { name: "Run" }));
    await waitFor(() => expect(onStarted).toHaveBeenCalledWith(STARTED));
    const [url, init] = f.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/workflows/demo/run");
    expect(init.method).toBe("POST");
    const body = sent(f);
    expect(body.inputs).toMatchObject({ title: "Hello", count: 3, ratio: 0.5, dry: true, meta: { k: [1, 2] } });
    expect(typeof body.inputs.count).toBe("number");
  });

  it("refuses a missing required input client-side, without calling the API", async () => {
    const f = fakeApi();
    const user = userEvent.setup();
    const { onStarted } = open();
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(f).not.toHaveBeenCalled();
    expect(onStarted).not.toHaveBeenCalled();
    expect(screen.getAllByText("Required")).toHaveLength(2);
    expect(screen.getByLabelText(/^title/)).toHaveAttribute("aria-invalid", "true");
  });

  it("refuses a decimal integer and malformed JSON inline", async () => {
    const f = fakeApi();
    const user = userEvent.setup();
    open();
    await user.type(screen.getByLabelText(/^title/), "x");
    await user.type(screen.getByLabelText(/^count/), "2.5");
    await user.click(screen.getByLabelText(/^meta/));
    await user.paste("{oops");
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(f).not.toHaveBeenCalled();
    expect(screen.getByText(/whole number/i)).toBeInTheDocument();
    expect(screen.getByText(/valid JSON/i)).toBeInTheDocument();
  });

  it("shows a 422 invalid_inputs path next to its field as guided text", async () => {
    fakeApi({
      status: 422,
      body: {
        error: {
          code: "invalid_inputs",
          message: "RAW SERVER TEXT",
          errors: [{ path: "inputs.title", code: "required", message: "RAW FIELD TEXT" }],
        },
      },
    });
    const user = userEvent.setup();
    const { onStarted } = open();
    await user.type(screen.getByLabelText(/^title/), "x");
    await user.type(screen.getByLabelText(/^count/), "1");
    await user.click(screen.getByRole("button", { name: "Run" }));
    const field = await screen.findByText("A required field is missing.");
    expect(field.closest("[data-field='title']")).not.toBeNull();
    expect(screen.queryByText(/RAW/)).toBeNull();
    expect(onStarted).not.toHaveBeenCalled();
  });

  it("a non-422 failure is guided at the top and Escape closes", async () => {
    fakeApi({ status: 409, body: { error: { code: "not_fireable", message: "RAW", errors: [] } } });
    const user = userEvent.setup();
    const { onClose } = open({ ...WF, inputs: [] });
    await user.click(screen.getByRole("button", { name: "Run" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("RAW")).toBeNull();
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalled();
  });
});

describe("RunOutputs", () => {
  it("renders scalars readably and objects pretty-printed in a collapsible", () => {
    render(<RunOutputs outputs={{ verdict: "approve", score: 7, report: { a: [1, 2] } }} />);
    expect(screen.getByText("verdict")).toBeInTheDocument();
    expect(screen.getByText("approve")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    const details = screen.getByText("report").closest("details");
    expect(details).not.toBeNull();
    expect(details!.querySelector("pre")!.textContent).toBe(JSON.stringify({ a: [1, 2] }, null, 2));
  });
  it("says so when there are no outputs", () => {
    render(<RunOutputs outputs={{}} />);
    expect(screen.getByText(/no outputs/i)).toBeInTheDocument();
  });
});
