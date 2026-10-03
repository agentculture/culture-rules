import { describe, expect, it } from "vitest";
import type { RuleDoc } from "../api/rules";
import { RULES } from "../fixtures/rules-fixture";
import {
  badgesFor,
  canRelate,
  relationText,
  relationsOf,
  withRelation,
  withoutRelation,
} from "./relations";

const rules = RULES as RuleDoc[];
const byId = (id: string) => rules.find((r) => r.id === id) as RuleDoc;
const nameOf = (id: string) => byId(id)?.name ?? id;

describe("relationsOf", () => {
  it("lists the relationships a rule declares and the ones pointing at it", () => {
    const build = relationsOf(rules, "build-and-publish");
    expect(build.outgoing).toEqual([
      { kind: "must_after", from: "build-and-publish", to: "review-on-approve" },
    ]);
    expect(build.incoming).toEqual([]);

    const review = relationsOf(rules, "review-on-approve");
    expect(review.outgoing).toEqual([]);
    expect(review.incoming).toEqual([
      { kind: "must_after", from: "build-and-publish", to: "review-on-approve" },
    ]);
  });

  it("reads supersedes and may_after the same way", () => {
    const set = rules.map((r) =>
      r.id === "train-batch" ? { ...r, supersedes: ["clean-caches"], may_after: ["triage-bugs"] } : r,
    );
    const train = relationsOf(set, "train-batch").outgoing;
    expect(train.map((r) => r.kind).sort()).toEqual(["may_after", "supersedes"]);
    expect(relationsOf(set, "clean-caches").incoming).toEqual([
      { kind: "supersedes", from: "train-batch", to: "clean-caches" },
    ]);
  });
});

describe("withRelation / withoutRelation", () => {
  it("adds a target once and removes it again, leaving the rest untouched", () => {
    const added = withRelation(byId("train-batch"), "may_after", "triage-bugs");
    expect(added.may_after).toEqual(["triage-bugs"]);
    expect(withRelation(added, "may_after", "triage-bugs").may_after).toEqual(["triage-bugs"]);
    const removed = withoutRelation(added, "may_after", "triage-bugs");
    expect(removed.may_after).toEqual([]);
    expect(removed.name).toBe("Train batch");
  });
});

describe("canRelate", () => {
  it("refuses a rule related to itself, an unknown rule and a duplicate", () => {
    expect(canRelate(rules, "train-batch", "must_after", "train-batch")).toMatch(/itself/);
    expect(canRelate(rules, "train-batch", "must_after", "ghost")).toMatch(/unknown/);
    expect(canRelate(rules, "build-and-publish", "must_after", "review-on-approve")).toMatch(
      /already/,
    );
    expect(canRelate(rules, "train-batch", "must_after", "triage-bugs")).toBeNull();
  });

  it("refuses a cycle in must_after and in supersedes", () => {
    expect(canRelate(rules, "review-on-approve", "must_after", "build-and-publish")).toMatch(
      /cycle/,
    );
    const set = rules.map((r) => (r.id === "train-batch" ? { ...r, supersedes: ["triage-bugs"] } : r));
    expect(canRelate(set, "triage-bugs", "supersedes", "train-batch")).toMatch(/cycle/);
  });
});

describe("badges on both ends", () => {
  it("words each end of the relationship", () => {
    const rel = { kind: "must_after", from: "build-and-publish", to: "review-on-approve" } as const;
    expect(relationText(rel, "out", nameOf)).toBe("must run after Review on approve");
    expect(relationText(rel, "in", nameOf)).toBe("Build and publish must run after this");
    const may = { kind: "may_after", from: "train-batch", to: "triage-bugs" } as const;
    expect(relationText(may, "out", nameOf)).toBe("may run after Triage bugs");
    expect(relationText(may, "in", nameOf)).toBe("Train batch may run after this");
    const sup = { kind: "supersedes", from: "train-batch", to: "clean-caches" } as const;
    expect(relationText(sup, "out", nameOf)).toBe("supersedes Clean caches");
    expect(relationText(sup, "in", nameOf)).toBe("superseded by Train batch");
  });

  it("badges the other end of a focused rule's relationships in the list", () => {
    expect(badgesFor(rules, "build-and-publish", "review-on-approve")).toEqual([
      { kind: "must_after", direction: "in", text: "must run first" },
    ]);
    expect(badgesFor(rules, "review-on-approve", "build-and-publish")).toEqual([
      { kind: "must_after", direction: "out", text: "waits for this" },
    ]);
    expect(badgesFor(rules, "build-and-publish", "train-batch")).toEqual([]);
  });
});
