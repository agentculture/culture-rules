import { beforeEach, describe, expect, it } from "vitest";
import {
  getAgentState,
  resetAgentState,
  serializeAgentState,
  setAgentState,
  subscribeAgentState,
} from "./store";

describe("agent-state store", () => {
  beforeEach(() => resetAgentState());

  it("starts loading with no errors", () => {
    expect(getAgentState()).toMatchObject({ status: "loading", errors: [] });
  });

  it("does not notify when nothing changed", () => {
    let calls = 0;
    const off = subscribeAgentState(() => (calls += 1));
    setAgentState({ view_ready: true, tab: "rules" });
    setAgentState({ view_ready: true, tab: "rules" });
    off();
    expect(calls).toBe(1);
  });

  it("derives ready from the view AND identity having loaded", () => {
    setAgentState({ view_ready: true, identity: { status: "loading", identity: null, kind: null, role: null } });
    expect(getAgentState().status).toBe("loading");
    setAgentState({ identity: { status: "signed-in", identity: "ori", kind: "sso", role: "admin" } });
    expect(getAgentState().status).toBe("ready");
  });

  it("escapes < so API data cannot close the script element", () => {
    setAgentState({ errors: ["</script><b>"] });
    expect(serializeAgentState(getAgentState())).not.toContain("</script>");
  });
});
