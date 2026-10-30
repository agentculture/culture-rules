import { render, screen, within } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import type { Actor } from "../api/actors";
import type { Action } from "../api/types";
import { ACTORS } from "../fixtures/rules-fixture";
import ActionPicker, { ACTION_KINDS, actorsFor, blankAction } from "./ActionPicker";

/* repodocs.sync (culture-rules#124): repos' devague record to the library, through a service actor with a repos_root. */

const REPODOCS_ACTORS: Actor[] = [
  ...ACTORS,
  {
    id: "culture-library-repodocs",
    name: "Culture library (repo documents)",
    kind: "service",
    enabled: true,
    params: { base_url: "http://127.0.0.1:8796", repos_root: "~/git" },
  },
  { id: "repodocs-off", name: "Old", kind: "service", enabled: false, params: { repos_root: "~/git" } },
  { id: "xteink", name: "xteink", kind: "service", enabled: true, params: { base_url: "http://localhost:8080" } },
];

function Harness() {
  const [value, setValue] = useState<Action>(blankAction());
  return <ActionPicker value={value} actors={REPODOCS_ACTORS} triggerType="schedule" onChange={setValue} />;
}

describe("repodocs.sync", () => {
  it("is listed in the picker", () => {
    render(<Harness />);
    const values = within(screen.getByLabelText("What happens"))
      .getAllByRole("option")
      .map((o) => o.getAttribute("value"));
    expect(values).toContain("repodocs.sync");
    expect(screen.getByRole("option", { name: "Push repo documents to the library" })).toBeTruthy();
  });

  it("offers only enabled service actors with a repos_root", () => {
    expect(actorsFor("repodocs.sync", REPODOCS_ACTORS).map((a) => a.id)).toEqual(["culture-library-repodocs"]);
  });

  it("takes only the actor, which is required", () => {
    expect(Object.keys(ACTION_KINDS["repodocs.sync"].params)).toEqual(["actor"]);
    expect(ACTION_KINDS["repodocs.sync"].params.actor.required).toBe(true);
  });
});
