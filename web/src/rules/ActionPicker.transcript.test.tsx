import { render, screen, within } from "@testing-library/react";
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
    expect(actionProblem({ kind: "llm.complete", params: { actor: "cortex", content: "text" } })).toBeNull();
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
  it("offers book, article and transcript", () => {
    expect(ACTION_KINDS["library.push"].params.kind.options?.map((o) => o.value)).toEqual([
      "book",
      "article",
      "transcript",
    ]);
  });
});
