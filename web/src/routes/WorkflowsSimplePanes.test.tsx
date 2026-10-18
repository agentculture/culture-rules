import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Workflows from "./Workflows";
import { resetAgentState } from "../agent-state/store";
import { createFakeApi, fetchFor, type FakeApi } from "../rules/fake-api";
import { DOMMatrixStub, MeasuringResizeObserver } from "../test/reactFlow";
import { VIEW_MODE_KEY } from "../workflows/views/mode";
import { REVIEW_PR, REVIEW_RULE_ID, WORKFLOW_RULES } from "../workflows/fixture";

/**
 * d14: in Simple, a step's text opens the panel Detailed opens for that step (editable, saved
 * through the page's draft with expected_revision), and a rule's sentence opens that rule's
 * editor with its enable switch. d13: Notes at the top of Detailed; the canvas legend only with
 * Detailed and Debug.
 */
const seg = (o: Record<string, unknown>) => ({ keys: [], tail_keys: [], depth: 0, tail: "", standin: false, fallback: false, ...o });
const SEGMENTS: Array<Record<string, unknown>> = [
  { part: "start", sentence: 0, keys: [], rule: REVIEW_RULE_ID, text: "It starts when a review is approved." },
  seg({ part: "step", sentence: 1, phrase: "First", step: "fetch-diff", name: "Fetch diff", text: "Fetches the diff.", shown: "fetches the diff", tail: "." }),
];
let paragraphReads = 0;

function seeded(): FakeApi {
  const api = createFakeApi();
  api.workflows = [{ ...structuredClone(REVIEW_PR), revision: 3 }];
  api.rules = structuredClone(WORKFLOW_RULES);
  return api;
}

function stub(api: FakeApi) {
  const inner = fetchFor(api);
  paragraphReads = 0;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (/\/workflows\/review-pr\/describe$/.test(String(input))) {
      paragraphReads += 1;
      const text = SEGMENTS.map((s) => String(s.text ?? "")).join("\n");
      return new Response(
        JSON.stringify({ id: "review-pr", kind: "workflow", lines: [], entries: [], paragraph: { segments: SEGMENTS, text } }),
        { status: 200, headers: { "content-type": "application/json" } },
      );
    }
    return inner(input, init);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function open() {
  return render(
    <MemoryRouter initialEntries={["/workflows?id=review-pr"]}>
      <Routes>
        <Route path="/workflows" element={<Workflows />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.removeItem(VIEW_MODE_KEY);
  resetAgentState();
  vi.stubGlobal("ResizeObserver", MeasuringResizeObserver);
  vi.stubGlobal("DOMMatrixReadOnly", DOMMatrixStub);
});
afterEach(() => {
  localStorage.removeItem(VIEW_MODE_KEY);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Simple opens the step panel (d14)", () => {
  it("opens the Detailed step panel, edits the description, saves with expected_revision, re-reads the paragraph, Escape returns focus", async () => {
    const api = seeded();
    const fetchMock = stub(api);
    open();
    const user = userEvent.setup();
    const text = await screen.findByRole("button", { name: "fetches the diff" });
    await user.click(text);
    const panel = await screen.findByRole("dialog", { name: "Edit Fetch diff" });
    expect(text).toHaveClass("sv-selected");
    const description = within(panel).getByRole("textbox", { name: "One-line description" });
    expect(within(panel).getByText(/Start with a verb/)).toBeInTheDocument();
    expect(within(panel).getByRole("textbox", { name: "Notes" })).toBeInTheDocument();
    await user.type(description, "Downloads the diff.");
    const reads = paragraphReads;
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.workflows[0].revision).toBe(4));
    expect(api.workflows[0].steps?.find((s) => s.id === "fetch-diff")?.description).toMatch(/Downloads the diff\./);
    const puts = fetchMock.mock.calls.filter(([, init]) => init?.method === "PUT");
    expect(JSON.parse(String(puts[0][1]?.body)).expected_revision).toBe(3);
    await waitFor(() => expect(paragraphReads).toBeGreaterThan(reads));
    // Escape closes the panel from inside it (as in Detailed); focus returns to the clicked text.
    await user.click(within(panel).getByRole("textbox", { name: "One-line description" }));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Edit Fetch diff" })).toBeNull());
    expect(screen.getByRole("button", { name: "fetches the diff" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "fetches the diff" })).not.toHaveClass("sv-selected");
  });
});

describe("Simple opens the rule editor (d14)", () => {
  it("shows the rule's editor in the right pane with its enable switch; disabling goes through the rule's own write", async () => {
    const api = seeded();
    stub(api);
    open();
    const user = userEvent.setup();
    const sentence = await screen.findByRole("button", { name: /It starts when a review is approved/ });
    await user.click(sentence);
    const panel = await screen.findByRole("dialog", { name: "Rule editor" });
    expect(sentence).toHaveClass("sv-selected");
    const toggle = await within(panel).findByRole("switch", { name: /enabled/i });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    await user.click(toggle);
    await waitFor(() => expect(api.rules.find((r) => r.id === REVIEW_RULE_ID)?.enabled).toBe(false));
    await user.click(within(panel).getByRole("button", { name: "Close" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Rule editor" })).toBeNull());
    expect(sentence).toHaveFocus();
  });

  it("one side pane at a time: a step then a rule", async () => {
    stub(seeded());
    open();
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "fetches the diff" }));
    await screen.findByRole("dialog", { name: "Edit Fetch diff" });
    await user.click(screen.getByRole("button", { name: /It starts when a review is approved/ }));
    await screen.findByRole("dialog", { name: "Rule editor" });
    expect(screen.queryByRole("dialog", { name: "Edit Fetch diff" })).toBeNull();
  });
});

describe("the views (d13)", () => {
  it("Simple shows no canvas legend; Detailed and Debug do; Notes sit at the top of Detailed and edit the draft", async () => {
    stub(seeded());
    open();
    const user = userEvent.setup();
    await screen.findByRole("button", { name: "fetches the diff" });
    expect(screen.queryByText(/same machine|crosses machines/)).toBeNull();
    expect(screen.queryByRole("textbox", { name: "Notes for this workflow" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Detailed" }));
    expect(await screen.findByText("crosses machines")).toBeInTheDocument();
    const notes = screen.getByRole("textbox", { name: "Notes for this workflow" });
    await user.type(notes, "Ask Dana.");
    expect(await screen.findByRole("button", { name: "Save" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Debug" }));
    expect(await screen.findByText("same machine")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Simple" }));
    await waitFor(() => expect(screen.queryByText("same machine")).toBeNull());
  });
});
