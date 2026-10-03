import { describe, expect, it } from "vitest";
import type { Actor } from "../api/actors";
import { ACTORS } from "./actors-fixture";
import { configSourceText, filterActors, kindOfFilter, parseCapabilities } from "./actors-view";

describe("actors-view", () => {
  it("filters by kind; 'all' keeps everyone, daemons included", () => {
    expect(filterActors(ACTORS, "all")).toHaveLength(8);
    expect(filterActors(ACTORS, "agent").map((a) => a.id)).toEqual(["claude-code", "codex", "colleague"]);
    expect(filterActors(ACTORS, "daemon").map((a) => a.id)).toEqual(["pr-watcher"]);
  });

  it("maps a pressed filter to its kind", () => {
    expect(kindOfFilter("all")).toBeNull();
    expect(kindOfFilter("human")).toBe("human");
  });

  it("describes the config source", () => {
    const repo = { id: "x", name: "X", kind: "agent", config_source: "repo", repo: "culture-rules" } as Actor;
    expect(configSourceText(repo)).toBe("culture-rules/culture.yaml");
    expect(configSourceText({ ...repo, repo: null })).toBe("no repo set");
    expect(configSourceText({ ...repo, config_source: "db" })).toBe("db record");
  });

  it("parses a comma list of capabilities, trimming and dropping blanks", () => {
    expect(parseCapabilities(" review, ,triage ,review")).toEqual(["review", "triage"]);
    expect(parseCapabilities("")).toEqual([]);
  });
});
