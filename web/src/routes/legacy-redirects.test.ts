import { describe, expect, it } from "vitest";
import type { Rule } from "../api/types";
import { ruleRedirect, workflowPath, NOTICE_RULE_NOT_FOUND } from "./legacy-redirects";

const rule = (id: string, workflow: string | null): Rule =>
  ({
    id,
    name: id,
    trigger: { kind: "event", params: { type: "github.pr.opened" } },
    condition: null,
    workflow: workflow === null ? null : { id: workflow, version: null, inputs: {} },
    action: { kind: "message", params: {} },
    enabled: true,
  }) as unknown as Rule;

const RULES = [rule("pr-fixer-checks", "pr-fix"), rule("jira to discord", null), rule("blank", "")];

describe("ruleRedirect: old /rules links land on the folded Workflows tab", () => {
  it("/rules (no id) lands on /workflows", () => {
    expect(ruleRedirect(undefined, RULES)).toEqual({ to: "/workflows" });
  });

  it("a rule with a workflow opens that workflow at its entry point", () => {
    expect(ruleRedirect("pr-fixer-checks", RULES)).toEqual({
      to: "/workflows/pr-fix?entry=pr-fixer-checks",
    });
  });

  it("a rule with no workflow lands on the D7 place: the list, with the rule chosen", () => {
    expect(ruleRedirect("jira to discord", RULES)).toEqual({ to: "/workflows?entry=jira%20to%20discord" });
    expect(ruleRedirect("blank", RULES)).toEqual({ to: "/workflows?entry=blank" });
  });

  it("an unknown id lands on /workflows with a not-found notice naming it", () => {
    expect(ruleRedirect("gone/rule", RULES)).toEqual({
      to: `/workflows?notice=${NOTICE_RULE_NOT_FOUND}&rule=gone%2Frule`,
    });
  });

  it("ids are encoded in the path and the query", () => {
    expect(ruleRedirect("a b", [rule("a b", "w/1")])).toEqual({ to: "/workflows/w%2F1?entry=a%20b" });
  });
});

describe("workflowPath: /workflows/:workflowId is the same page as /workflows?id=", () => {
  it("moves the path id into the query and keeps the other params", () => {
    expect(workflowPath("pr-fix", "?entry=pr-fixer-checks")).toBe("/workflows?id=pr-fix&entry=pr-fixer-checks");
    expect(workflowPath("w/1", "")).toBe("/workflows?id=w%2F1");
  });

  it("the path id wins over a stale ?id=", () => {
    expect(workflowPath("b", "?id=a&run=r1")).toBe("/workflows?id=b&run=r1");
  });
});
