import { readFileSync } from "node:fs";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Description, Paragraph, ParagraphSegment } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { useLiveFeed } from "./liveFeed";
import SimpleView from "./SimpleView";

const seg = (o: Record<string, unknown>) => ({ keys: [], depth: 0, ...o }) as unknown as ParagraphSegment;
const SEGMENTS: ParagraphSegment[] = [
  { part: "start", keys: ["starts_event"], text: "It starts when pr.opened arrives." },
  seg({ part: "step", phrase: "First:", step: "fetch", name: "Fetch", text: "Fetches the PR.", gap: false, fallback: false, grouped: false }),
  seg({ part: "step", phrase: "Then, if it is large:", step: "split", name: "Split", text: "Splits it.", gap: false, fallback: false, grouped: false }),
  seg({ part: "step", phrase: "; then", step: "tag", name: "Tag", text: "Tags the pieces.", gap: false, fallback: false, grouped: true }),
  seg({ part: "step", phrase: "Each time:", step: "inner", name: "Inner", text: "Works on one piece.", depth: 1, gap: false, fallback: false, grouped: false }),
  seg({ part: "step", phrase: "Then:", step: "ship", name: "Ship", text: "", gap: true, fallback: false, grouped: false }),
  seg({ part: "step", phrase: "Disabled:", step: "old", name: "Old", text: "Is retired.", gap: false, fallback: false, grouped: false, disabled: true }),
  { part: "then", keys: ["ends_noop"], text: "When it is done, nothing else happens." },
];
// What the API's paragraph.text reads for those segments (grouping edits applied).
const TEXT =
  "It starts when pr.opened arrives. First: Fetches the PR. Then, if it is large: Splits it; then tags the pieces." +
  " Each time: Works on one piece. Then: [Ship has no description yet] Disabled: Is retired. When it is done, nothing else happens.";

const WF: WorkflowDef = {
  id: "pr-fix",
  name: "PR fix",
  description: "Fixes a pull request.",
  steps: [
    { id: "fetch", kind: "logic", description: "Fetches the PR." },
    { id: "ship", kind: "logic" },
  ],
};

let puts: unknown[] = [];
let describes = 0;
function stubApi(paragraph: Paragraph = { segments: SEGMENTS, text: TEXT }, fail = false) {
  puts = [];
  describes = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = (b: unknown, status = 200) =>
        new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
      if (String(url).endsWith("/describe")) {
        describes += 1;
        if (fail) return body({ detail: "nope" }, 500);
        const d: Description = { id: "pr-fix", kind: "workflow", description: "", paragraph, lines: [], entries: [] };
        return body(d);
      }
      if (init?.method === "PUT") {
        const sent = JSON.parse(String(init.body));
        puts.push(sent);
        return body(sent);
      }
      return body({}, 404);
    }),
  );
}
afterEach(() => vi.unstubAllGlobals());

/** The paragraph as the reader sees it: whitespace normalized, visually-hidden cues left out. */
function visible(el: HTMLElement) {
  const clone = el.cloneNode(true) as HTMLElement;
  clone.querySelectorAll(".sv-sr").forEach((n) => n.remove());
  return (clone.textContent ?? "").replace(/\s+/g, " ").trim();
}

describe("Simple is text only (d1)", () => {
  it("renders no entry cards, no steps column and no Then column", async () => {
    stubApi();
    const { container } = render(<SimpleView def={WF} />);
    await screen.findByTestId("paragraph");
    expect(container.querySelector(".fold-board, .fold-col, .fold-steps, .fold-entry")).toBeNull();
    expect(container.querySelector(".react-flow")).toBeNull();
    expect(screen.queryByRole("heading", { name: "When" })).toBeNull();
    expect(screen.queryByText(/same machine|crosses machines/i)).toBeNull();
  });
});

describe("the workflow description", () => {
  it("shows the description, or a visible gap", async () => {
    stubApi();
    const { unmount } = render(<SimpleView def={WF} />);
    expect(await screen.findByText("Fixes a pull request.")).toBeInTheDocument();
    unmount();
    render(<SimpleView def={{ ...WF, description: "" }} />);
    expect(await screen.findByText("No description yet")).toHaveClass("sv-gap");
  });

  it("is editable in place and saved through the workflow save, then the paragraph is read again", async () => {
    stubApi();
    const onSaved = vi.fn();
    render(<SimpleView def={WF} onSaved={onSaved} />);
    await screen.findByTestId("paragraph");
    const before = describes;
    const user = userEvent.setup();
    await user.click(screen.getByText("Fixes a pull request."));
    const field = screen.getByRole("textbox", { name: /description of this workflow/ });
    await user.clear(field);
    await user.type(field, "Repairs a PR.");
    await user.tab();
    await waitFor(() => expect(puts).toHaveLength(1));
    expect((puts[0] as WorkflowDef).description).toBe("Repairs a PR.");
    expect(onSaved).toHaveBeenCalled();
    await waitFor(() => expect(describes).toBeGreaterThan(before));
  });
});

describe("Simple view: saves send the revision read (t16)", () => {
  it("two tabs on revision 3: the first saves (sending 3), the second gets the conflict and keeps its typed text until Reload", async () => {
    stubApi();
    let stored = 3;
    const base = globalThis.fetch;
    vi.stubGlobal("fetch", async (url: string, init?: RequestInit) => {
      const json = (b: unknown, status = 200) =>
        new Response(JSON.stringify(b), { status, headers: { "content-type": "application/json" } });
      if (init?.method === "PUT") {
        const sent = JSON.parse(String(init.body));
        if (sent.expected_revision !== stored) {
          const errors = [{ path: "expected_revision", code: "revision_conflict", message: "stale", current_revision: stored }];
          return json({ error: { code: "revision_conflict", message: "stale", errors } }, 409);
        }
        stored += 1;
        const { expected_revision: _e, ...doc } = sent;
        return json({ ...doc, revision: stored });
      }
      if (String(url).endsWith("/workflows/pr-fix")) return json({ ...WF, description: "Tab one wrote this.", revision: stored });
      return base(url, init);
    });
    const def = { ...WF, revision: 3 };
    const savedOne = vi.fn();
    const savedTwo = vi.fn();
    const one = render(<SimpleView def={def} onSaved={savedOne} />);
    const two = render(<SimpleView def={def} onSaved={savedTwo} />);
    const userOne = userEvent.setup();
    const userTwo = userEvent.setup();
    const edit = async (view: typeof one, user: typeof userOne, text: string) => {
      await within(view.container).findByTestId("paragraph");
      await user.click(within(view.container).getByText("Fixes a pull request."));
      const field = within(view.container).getByRole("textbox", { name: /description of this workflow/ });
      await user.clear(field);
      await user.type(field, text);
      return field;
    };
    await edit(one, userOne, "Tab one wrote this.");
    const fieldTwo = await edit(two, userTwo, "Tab two wrote this.");
    await userOne.tab();
    await waitFor(() => expect(savedOne).toHaveBeenCalled());
    expect(savedOne.mock.calls[0][0].revision).toBe(4);

    await userTwo.tab();
    expect(await within(two.container).findByText("This workflow changed since you opened it.", { exact: false })).toBeInTheDocument();
    expect(fieldTwo).toHaveValue("Tab two wrote this.");
    expect(savedTwo).not.toHaveBeenCalled();

    await userTwo.click(within(two.container).getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(savedTwo).toHaveBeenCalledWith(expect.objectContaining({ revision: 4 })));
    expect(within(two.container).queryByText("This workflow changed since you opened it.", { exact: false })).toBeNull();
  });
});

describe("How it runs: the API paragraph", () => {
  it("the visible text equals the API's paragraph.text", async () => {
    stubApi();
    render(<SimpleView def={WF} />);
    const para = await screen.findByTestId("paragraph");
    expect(visible(para)).toBe(TEXT);
  });

  it("styles phrases, shows a gap naming the step, indents a loop body, strikes a disabled step", async () => {
    stubApi();
    render(<SimpleView def={WF} />);
    const para = await screen.findByTestId("paragraph");
    expect(within(para).getByText("First:")).toHaveClass("sv-phrase");
    expect(within(para).getByText("; then")).toHaveClass("sv-phrase");
    expect(within(para).getByText("[Ship has no description yet]")).toHaveClass("sv-gap");
    expect(within(para).getByText("Works on one piece.").closest(".sv-body")).not.toBeNull();
    const old = within(para).getByText("Is retired.").closest(".sv-step")!;
    expect(old).toHaveClass("sv-disabled");
    expect(old.querySelector(".sv-sr")).not.toBeNull();
  });

  it("a step description is edited in place: one PUT with that step's description, then a re-fetch", async () => {
    stubApi();
    const onSaved = vi.fn();
    render(<SimpleView def={WF} onSaved={onSaved} />);
    await screen.findByTestId("paragraph");
    const before = describes;
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Fetches the PR." }));
    const field = screen.getByRole("textbox", { name: "Edit the description of Fetch" });
    await user.clear(field);
    await user.type(field, "Downloads the PR.{Enter}");
    await waitFor(() => expect(puts).toHaveLength(1));
    const sent = puts[0] as WorkflowDef;
    expect(sent.steps?.find((s) => s.id === "fetch")?.description).toBe("Downloads the PR.");
    expect(sent.description).toBe("Fixes a pull request.");
    await waitFor(() => expect(describes).toBeGreaterThan(before));
  });

  it("a gap is filled the same way", async () => {
    stubApi();
    render(<SimpleView def={WF} />);
    const user = userEvent.setup();
    await user.click(await screen.findByText("[Ship has no description yet]"));
    await user.type(screen.getByRole("textbox", { name: "Edit the description of Ship" }), "Ships it.{Enter}");
    await waitFor(() => expect(puts).toHaveLength(1));
    expect((puts[0] as WorkflowDef).steps?.find((s) => s.id === "ship")?.description).toBe("Ships it.");
  });

  it("Escape saves nothing", async () => {
    stubApi();
    render(<SimpleView def={WF} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Fetches the PR." }));
    await user.type(screen.getByRole("textbox", { name: "Edit the description of Fetch" }), "x{Escape}");
    expect(puts).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Fetches the PR." })).toBeInTheDocument();
  });

  it("reads the paragraph again when a rule or workflow changes live", async () => {
    stubApi();
    function Host() {
      const feed = useLiveFeed();
      (globalThis as { __feed?: typeof feed }).__feed = feed;
      return <SimpleView def={WF} live={feed} />;
    }
    render(<Host />);
    await screen.findByTestId("paragraph");
    const before = describes;
    act(() => {
      (globalThis as unknown as { __feed: ReturnType<typeof useLiveFeed> }).__feed.emit([
        { collection: "rules", id: "r1", op: "update" } as never,
      ]);
    });
    await waitFor(() => expect(describes).toBeGreaterThan(before));
  });

  it("says so when the paragraph cannot be read", async () => {
    stubApi(undefined, true);
    render(<SimpleView def={WF} />);
    expect(await screen.findByText(/Could not read how this workflow runs/)).toBeInTheDocument();
  });
});

describe("the editor builds no phrases (o4)", () => {
  it("no joining phrase from the API's table is written in the Simple sources", () => {
    const src = ["SimpleView.tsx", "paragraphText.ts"].map((f) => readFileSync(`${process.cwd()}/src/workflows/simple/${f}`, "utf8")).join("\n");
    for (const phrase of ["First:", "At the same time", "It starts when", "Once both are done", "Depending on the conditions", "Carrying on even if"]) {
      expect(src).not.toContain(phrase);
    }
  });
});
