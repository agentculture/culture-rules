import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor } from "../api/actors";
import type { Action } from "../api/types";
import { ACTORS } from "../fixtures/rules-fixture";
import ActionPicker, { ACTION_KINDS, actionProblem, actorsFor, blankAction } from "./ActionPicker";

/* The transcripts-on-the-reader params: llm.complete's excerpt and brief, transcript.render, library.push kind. */

const READER_ACTORS: Actor[] = [
  ...ACTORS,
  { id: "cortex", name: "Cortex", kind: "agent", enabled: true, params: { base_url: "http://localhost:8001" } },
  { id: "stt", name: "STT", kind: "agent", enabled: true, params: { base_url: "http://localhost:8002", output_root: "/data/t" } },
  { id: "xteink", name: "xteink", kind: "service", enabled: true, params: { base_url: "http://localhost:8080" } },
];

function Harness() {
  const [value, setValue] = useState<Action>(blankAction());
  return (
    <>
      <ActionPicker value={value} actors={READER_ACTORS} triggerType="schedule" onChange={setValue} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Action;

describe("llm.complete on a transcript", () => {
  it("needs content or content_path, not both", () => {
    expect(actionProblem({ kind: "llm.complete", params: { actor: "cortex" } })).toBe("empty");
    expect(actionProblem({ kind: "llm.complete", params: { actor: "cortex", content: "text", brief: "notes" } })).toBeNull();
    expect(
      actionProblem({
        kind: "llm.complete",
        params: { actor: "cortex", content_path: "/data/t/x.json", content_root: "/data/t", brief: "transcript" },
      }),
    ).toBeNull();
  });

  it("lists the excerpt params and the brief", () => {
    expect(Object.keys(ACTION_KINDS["llm.complete"].params)).toEqual(
      expect.arrayContaining(["brief", "content_path", "content_root", "content_max_chars", "not_after"]),
    );
    expect(ACTION_KINDS["llm.complete"].params.content.required).toBeFalsy();
  });
});

describe("transcript.render", () => {
  it("is listed in the picker without an actor", () => {
    render(<Harness />);
    const values = within(screen.getByLabelText("What happens"))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(values).toContain("transcript.render");
  });

  it("may narrow by an actor with a transcripts or output root", () => {
    expect(actorsFor("transcript.render", READER_ACTORS).map((a) => a.id)).toEqual(["stt"]);
  });

  it("saves its path, root and recording time", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("What happens"), "transcript.render");
    await user.type(screen.getByLabelText("Result path"), "/data/t/x.json");
    await user.type(screen.getByLabelText("Root"), "/data/t");
    await user.type(screen.getByLabelText("Recorded at"), "2026-10-24T06:05:00Z");
    expect(saved()).toEqual({
      kind: "transcript.render",
      params: { result_path: "/data/t/x.json", root: "/data/t", recorded_at: "2026-10-24T06:05:00Z" },
    });
    expect(actionProblem(saved())).toBeNull();
  });

  it("needs its required params", () => {
    expect(actionProblem({ kind: "transcript.render", params: { result_path: "/x" } })).toBe("empty");
  });
});

describe("library.push kind", () => {
  it("is required and listed by the library actor, not built in", () => {
    const kind = ACTION_KINDS["library.push"].params.kind;
    expect(kind.required).toBe(true);
    expect(kind.options).toBeUndefined();
    expect(kind.optionsFrom).toBe("kinds");
    expect(ACTION_KINDS["library.push"].params.visibility.optionsFrom).toBe("visibilities");
  });
});

describe("the library-item pieces (library items, t7)", () => {
  it("reads llm.complete's brief from the actor, with answer and cache fields", () => {
    const params = ACTION_KINDS["llm.complete"].params;
    expect(params.brief.options).toBeUndefined();
    expect(params.brief.optionsFrom).toBe("briefs");
    expect(params.brief.required).toBe(true);
    expect(params.answer.type).toBe("json");
    expect(params.cache.type).toBe("bool");
    expect(params.cache_name.type).toBe("str");
    expect(params.cache_label.type).toBe("str");
  });

  it("lets transcript.render write Markdown and audio.transcribe go without a hash", () => {
    expect(ACTION_KINDS["transcript.render"].params.format.options?.map((o) => o.value)).toEqual([
      "epub",
      "md",
    ]);
    expect(ACTION_KINDS["audio.transcribe"].params.audio_sha256.required).toBeFalsy();
  });

  it("lets library.push pick a format from the actor and replace an item by id", () => {
    const params = ACTION_KINDS["library.push"].params;
    expect(params.format.optionsFrom).toBe("formats");
    expect(params.item_id.type).toBe("int");
  });
});

describe("llm.complete's brief names come from the chosen actor", () => {
  const withBriefs: Actor[] = [
    ...READER_ACTORS,
    {
      id: "writer",
      name: "Writer",
      kind: "agent",
      enabled: true,
      params: { base_url: "http://localhost:8003", briefs: { notes: "text", digest: "text" } },
    },
  ];

  it("lists the actor's brief names and no built-in or default one", async () => {
    const user = userEvent.setup();
    const Wb = () => {
      const [value, setValue] = useState<Action>({ kind: "llm.complete", params: { actor: "writer", content: "x" } });
      return <ActionPicker value={value} actors={withBriefs} triggerType="schedule" onChange={setValue} />;
    };
    render(<Wb />);
    const select = screen.getByLabelText(/brief/i, { selector: "select" });
    expect(within(select).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["", "notes", "digest"]);
    await user.selectOptions(select, "digest");
    expect((select as HTMLSelectElement).value).toBe("digest");
  });
});

describe("editable model options", () => {
  function Model() {
    const [value, setValue] = useState<Action>({ kind: "llm.complete", params: {
      actor: "a", brief: "notes", content_path: "/data/input.json", content_root: "/data",
    } });
    const actors: Actor[] = [
      { id: "a", name: "A", kind: "agent", enabled: true, params: { base_url: "http://localhost", briefs: { notes: "Text" } } },
      { id: "b", name: "B", kind: "agent", enabled: true, params: { base_url: "http://localhost", briefs: { digest: "Text" } } },
      { id: "c", name: "C", kind: "agent", enabled: true, params: { base_url: "http://localhost" } },
    ];
    return <><ActionPicker value={value} actors={actors} onChange={setValue} /><output data-testid="saved">{JSON.stringify(value)}</output></>;
  }

  it("saves and edits an answer shape as an object, with cache options", async () => {
    render(<Model />);
    const user = userEvent.setup();
    const answer = { shape: { type: "text", max_chars: 50 } };
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: JSON.stringify(answer) } });
    expect(saved().params?.answer).toEqual(answer);
    await user.click(screen.getByLabelText("Keep the first answer for this file"));
    await user.type(screen.getByLabelText("Cache name"), "notes");
    await user.type(screen.getByLabelText("Cache label"), "Notes");
    expect(saved().params).toMatchObject({ answer, cache: true, cache_name: "notes", cache_label: "Notes" });
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "{" } });
    expect(actionProblem(saved())).toBe("invalid_value");
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: JSON.stringify({ shape: { type: "int" } }) } });
    expect(saved().params?.answer).toEqual({ shape: { type: "int" } });
    fireEvent.change(screen.getByLabelText("Answer"), { target: { value: "" } });
    expect(saved().params).not.toHaveProperty("answer");
  });

  it("requires a brief even when content is present", () => {
    expect(actionProblem({ kind: "llm.complete", params: { actor: "a", content: "Text" } })).toBe("empty");
  });

  it("clears unavailable briefs on actor changes without choosing a default", async () => {
    render(<Model />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Actor"), "b");
    expect(saved().params).not.toHaveProperty("brief");
    expect(within(screen.getByLabelText("Brief")).getAllByRole("option").map(o => o.getAttribute("value"))).toEqual(["", "digest"]);
    await user.selectOptions(screen.getByLabelText("Brief"), "digest");
    await user.selectOptions(screen.getByLabelText("Actor"), "c");
    expect(within(screen.getByLabelText("Brief")).getAllByRole("option")).toHaveLength(1);
    expect(actionProblem(saved())).toBe("empty");
  });
});
