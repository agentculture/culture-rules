import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import { groupProblems } from "./SharedForms";

const refused = (...paths: string[]) =>
  new ApiError(
    422,
    "invalid_rule",
    "rule is invalid",
    paths.map((path) => ({ path, code: "invalid", message: `bad ${path}` })),
  );

describe("groupProblems (issue #29 a, d8)", () => {
  it("places the validator's top-level paths at their fields", () => {
    expect(groupProblems(refused("exclusive_group"), { exclusive_group: "" })).toEqual({
      exclusive_group: "bad exclusive_group",
    });
    expect(groupProblems(refused("priority"), { priority: 2 })).toEqual({ priority: "bad priority" });
  });

  it("reads a request-body path (body.priority) as the top-level field", () => {
    expect(groupProblems(refused("body.priority"), { priority: 2 })).toEqual({ priority: "bad body.priority" });
  });

  it("never places a nested path that merely ends in a field name at that field", () => {
    expect(groupProblems(refused("placement.priority"), { exclusive_group: "deploy" })).toEqual({
      exclusive_group: "rule is invalid",
    });
  });
});
