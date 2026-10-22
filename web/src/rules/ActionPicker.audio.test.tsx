import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor } from "../api/actors";
import type { Action } from "../api/types";
import { ACTORS } from "../fixtures/rules-fixture";
import ActionPicker, { actionProblem, actorsFor, blankAction } from "./ActionPicker";

/* audio.transcribe: speech-to-text through an agent actor that carries a base_url. */

const AUDIO_ACTORS: Actor[] = [
  ...ACTORS,
  { id: "whisper", name: "Whisper", kind: "agent", enabled: true, params: { base_url: "http://localhost:8001" } },
  { id: "whisper-off", name: "Old whisper", kind: "agent", enabled: false, params: { base_url: "http://localhost:8002" } },
  { id: "plain-agent", name: "Plain agent", kind: "agent", enabled: true },
  { id: "xteink", name: "xteink", kind: "service", enabled: true, params: { base_url: "http://localhost:8080" } },
];

function Harness() {
  const [value, setValue] = useState<Action>(blankAction());
  return (
    <>
      <ActionPicker value={value} actors={AUDIO_ACTORS} triggerType="schedule" onChange={setValue} />
      <output data-testid="saved">{JSON.stringify(value)}</output>
    </>
  );
}

const saved = () => JSON.parse(screen.getByTestId("saved").textContent ?? "null") as Action;

describe("audio.transcribe", () => {
  it("is listed in the picker", () => {
    render(<Harness />);
    const values = within(screen.getByLabelText("What happens"))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(values).toContain("audio.transcribe");
    expect(screen.getByRole("option", { name: "Transcribe an audio file" })).toBeTruthy();
  });

  it("offers only enabled agent actors with a base_url", () => {
    expect(actorsFor("audio.transcribe", AUDIO_ACTORS).map((a) => a.id)).toEqual(["whisper"]);
  });

  it("saves the audio path, hash and output directory", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("What happens"), "audio.transcribe");
    await user.selectOptions(screen.getByLabelText("Actor"), "whisper");
    await user.type(screen.getByLabelText("Audio path"), "/data/a.mp3");
    await user.type(screen.getByLabelText("Audio sha256"), "abc123");
    await user.type(screen.getByLabelText("Output dir"), "/data/out");
    await user.type(screen.getByLabelText("Language"), "en");
    expect(saved()).toEqual({
      kind: "audio.transcribe",
      params: {
        actor: "whisper",
        audio_path: "/data/a.mp3",
        audio_sha256: "abc123",
        output_dir: "/data/out",
        language: "en",
      },
    });
    expect(actionProblem(saved())).toBeNull();
  });

  it("needs its required params", () => {
    expect(actionProblem({ kind: "audio.transcribe", params: { actor: "whisper" } })).toBe("empty");
  });
});
