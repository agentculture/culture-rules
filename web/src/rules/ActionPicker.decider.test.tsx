import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor } from "../api/actors";
import type { Action } from "../api/types";
import { ACTORS } from "../fixtures/rules-fixture";
import ActionPicker, { ACTION_KINDS, actorsFor, blankAction } from "./ActionPicker";

/* decider.assess: calibrated probabilities from the decider, through an agent actor with a base_url. */

const DECIDER_ACTORS: Actor[] = [
  ...ACTORS,
  { id: "decider", name: "Decider", kind: "agent", enabled: true, params: { base_url: "http://localhost:8003" } },
  { id: "decider-off", name: "Old decider", kind: "agent", enabled: false, params: { base_url: "http://localhost:8004" } },
  { id: "plain-agent", name: "Plain agent", kind: "agent", enabled: true },
  { id: "xteink", name: "xteink", kind: "service", enabled: true, params: { base_url: "http://localhost:8080" } },
];

function Harness() {
  const [value, setValue] = useState<Action>(blankAction());
  return <ActionPicker value={value} actors={DECIDER_ACTORS} triggerType="schedule" onChange={setValue} />;
}

describe("decider.assess", () => {
  it("is listed in the picker", () => {
    render(<Harness />);
    const values = within(screen.getByLabelText("What happens"))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(values).toContain("decider.assess");
    expect(screen.getByRole("option", { name: "Ask the decider" })).toBeTruthy();
  });

  it("offers only enabled agent actors with a base_url", () => {
    expect(actorsFor("decider.assess", DECIDER_ACTORS).map((a) => a.id)).toEqual(["decider"]);
  });

  it("declares actor, state and questions as required and not_after as optional", () => {
    const p = ACTION_KINDS["decider.assess"].params;
    expect(p.actor.required).toBe(true);
    expect(p.state.required).toBe(true);
    expect(p.questions.required).toBe(true);
    expect(p.not_after.required).toBeFalsy();
  });

  it("shows the state and questions fields", async () => {
    render(<Harness />);
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("What happens"), "decider.assess");
    expect(screen.getByLabelText("Actor")).toBeTruthy();
    expect(screen.getByLabelText("State")).toBeTruthy();
    expect(screen.getByLabelText("Questions")).toBeTruthy();
    expect(screen.getByLabelText("Not after")).toBeTruthy();
  });
});
