import { readFileSync } from "node:fs";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Description, Paragraph, ParagraphSegment } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { useLiveFeed } from "./liveFeed";
import SimpleView from "./SimpleView";

const seg = (o: Record<string, unknown>) =>
  ({ keys: [], tail_keys: [], depth: 0, tail: "", standin: false, fallback: false, grouped: false, ...o }) as unknown as ParagraphSegment;
const SEGMENTS: ParagraphSegment[] = [
  { part: "start", sentence: 0, keys: ["starts_event"], rule: "pr-opened", text: "It starts when pr.opened arrives." },
  seg({ part: "step", sentence: 1, phrase: "First", step: "fetch", name: "Fetch", text: "Fetches the PR.", shown: "fetches the PR" }),
  seg({ part: "step", sentence: 1, phrase: "; then", step: "tag", name: "Tag", text: "Tags the pieces.", shown: "tags the pieces", tail: ", waiting 5 minutes.", grouped: true }),
  seg({ part: "step", sentence: 2, phrase: "Then, if it is large,", step: "split", name: "Split", text: "Splits it.", shown: "splits it", tail: "." }),
  seg({ part: "step", sentence: 3, phrase: "Then", step: "inner", name: "Inner", text: "Works on one piece.", shown: "works on one piece", tail: ".", depth: 1 }),
  seg({ part: "step", sentence: 4, phrase: "Then", step: "ship", name: "Ship", text: "Ship", shown: "ship", tail: ".", standin: true, gap: true }),
  seg({ part: "step", sentence: 5, phrase: "Then", step: "old", name: "Old", text: "Is retired.", shown: "is retired", tail: ".", disabled: true }),
  { part: "then", sentence: 6, keys: ["ends_noop"], text: "When it is done, nothing else happens." },
];
// What the API's paragraph.text reads for those segments: one sentence per line.
const TEXT = [
  "It starts when pr.opened arrives.",
  "First fetches the PR; then tags the pieces, waiting 5 minutes.",
  "Then, if it is large, splits it.",
  "Then works on one piece.",
  "Then ship.",
  "Then is retired.",
  "When it is done, nothing else happens.",
].join("\n");

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
  clone.querySelectorAll(".sv-sr, .sv-pill").forEach((n) => n.remove());
  return (clone.textContent ?? "")
    .split("\n")
    .map((l) => l.replace(/\s+/g, " ").trim())
    .filter(Boolean)
    .join("\n");
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

  it("one line per sentence; styles phrases, marks a stand-in not written yet, mutes a disabled step with a small tag, no strike-through", async () => {
    stubApi();
    render(<SimpleView def={WF} />);
    const para = await screen.findByTestId("paragraph");
    expect(para.querySelectorAll(".sv-line")).toHaveLength(7);
    expect(within(para).getByText("First")).toHaveClass("sv-phrase");
    expect(within(para).getByText("; then")).toHaveClass("sv-phrase");
    const standin = within(para).getByRole("button", { name: /ship/ });
    expect(standin).toHaveClass("sv-standin");
    expect(standin.querySelector(".sv-sr")?.textContent).toMatch(/not written yet/);
    expect(within(para).getByText("works on one piece").closest(".sv-line--in")).not.toBeNull();
    const old = within(para).getByText("is retired").closest(".sv-step")!;
    expect(old).toHaveClass("sv-disabled");
    expect(old.querySelector(".sv-sr")).not.toBeNull();
    // d13 (amends d8): muted and tagged, not struck through.
    expect(old.querySelector(".sv-pill")).toHaveTextContent("disabled");
    expect(getComputedStyle(old).textDecorationLine).not.toBe("line-through");
    expect(para.querySelectorAll(".sv-pill")).toHaveLength(1);
  });

  it("a step's text opens its panel (the page mounts the Detailed one); nothing is edited inline (d14)", async () => {
    stubApi();
    const onOpenStep = vi.fn();
    render(<SimpleView def={WF} onOpenStep={onOpenStep} />);
    const user = userEvent.setup();
    const text = await screen.findByRole("button", { name: "fetches the PR" });
    await user.click(text);
    expect(onOpenStep).toHaveBeenCalledWith("fetch", text);
    expect(screen.queryByRole("textbox", { name: /Edit the description of Fetch/ })).toBeNull();
    // A stand-in opens the same panel, by the keyboard too.
    await user.click(screen.getByRole("button", { name: /ship/ }));
    expect(onOpenStep).toHaveBeenLastCalledWith("ship", expect.anything());
    screen.getByRole("button", { name: "tags the pieces" }).focus();
    await user.keyboard("{Enter}");
    expect(onOpenStep).toHaveBeenLastCalledWith("tag", expect.anything());
    expect(puts).toHaveLength(0);
  });

  it("the selected step's words are highlighted while its panel is open", async () => {
    stubApi();
    const { rerender } = render(<SimpleView def={WF} selectedStep="fetch" />);
    const para = await screen.findByTestId("paragraph");
    expect(within(para).getByRole("button", { name: "fetches the PR" })).toHaveClass("sv-selected");
    expect(within(para).getByRole("button", { name: "tags the pieces" })).not.toHaveClass("sv-selected");
    rerender(<SimpleView def={WF} selectedStep={null} />);
    expect(para.querySelector(".sv-selected")).toBeNull();
  });

  it("a rule's sentence carries its rule id and opens the rule's editor", async () => {
    stubApi();
    const onOpenRule = vi.fn();
    const { rerender } = render(<SimpleView def={WF} onOpenRule={onOpenRule} />);
    const sentence = await screen.findByRole("button", { name: /It starts when pr.opened arrives/ });
    await userEvent.setup().click(sentence);
    expect(onOpenRule).toHaveBeenCalledWith("pr-opened", sentence);
    rerender(<SimpleView def={WF} onOpenRule={onOpenRule} selectedRule="pr-opened" />);
    expect(sentence).toHaveClass("sv-selected");
    // A closing sentence belongs to no single rule: plain words.
    expect(screen.queryByRole("button", { name: /nothing else happens/ })).toBeNull();
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
  it("lowercasing and stand-in logic are gone from the editor", () => {
    const src = readFileSync(`${process.cwd()}/src/workflows/simple/SimpleView.tsx`, "utf8");
    expect(src).not.toMatch(/toLowerCase|lowerFirst|has no description yet/);
  });

  it("no joining phrase from the API's table is written in the Simple sources", () => {
    const src = ["SimpleView.tsx"].map((f) => readFileSync(`${process.cwd()}/src/workflows/simple/${f}`, "utf8")).join("\n");
    for (const phrase of ["First:", "At the same time", "It starts when", "Once both are done", "Depending on the conditions", "Carrying on even if"]) {
      expect(src).not.toContain(phrase);
    }
  });
});
