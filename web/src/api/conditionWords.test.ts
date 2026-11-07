import { describe, expect, it } from "vitest";
import { conditionText, upstreamVars } from "../routes/rules-view";
import { guardText } from "../workflows/list/presentation";
import { conditionRows } from "../workflows/simple/text";
import { isItemCondition, itemConditionWords, itemWords } from "./conditionWords";
import type { Condition, Operand, Rule } from "./types";

/* The condition ops the editor reads but does not build, in the words describe.py says. */

const TITLE_HAS_BUG: Condition = { op: "contains", value: { field: "title" }, needle: { literal: "bug" } };
const HOST_IN_DOMAIN: Condition = { op: "host_suffix", value: { item: "host" }, suffix: { literal: "example.com" } };
const ANY_LINK: Condition = { op: "any", items: { field: "links" }, where: HOST_IN_DOMAIN };
const ALL_TAGS: Condition = { op: "all", items: { var: "tags" }, where: { op: "contains", value: { item: "." }, needle: { var: "word" } } };

const plain = (o: Operand): string => {
  if ("item" in o) return itemWords(o.item);
  if ("field" in o) return o.field;
  if ("var" in o) return `vars.${o.var}`;
  return String(o.literal);
};
const words = (c: Condition): string => (isItemCondition(c) ? itemConditionWords(c, plain, words) : c.op);

describe("item condition words", () => {
  it.each([
    [TITLE_HAS_BUG, "title contains bug"],
    [HOST_IN_DOMAIN, "its host is in the domain example.com"],
    [ANY_LINK, "for at least one of links, its host is in the domain example.com"],
    [ALL_TAGS, "for every one of vars.tags, it contains vars.word"],
  ])("say %j as describe.py does", (c, text) => {
    expect(words(c)).toBe(text);
  });

  it("read a nested item path by its last segment", () => {
    expect(itemWords("link.host")).toBe("its host");
  });

  it("tell the ops the editor only reads from the ones it builds", () => {
    expect([TITLE_HAS_BUG, HOST_IN_DOMAIN, ANY_LINK, ALL_TAGS].every(isItemCondition)).toBe(true);
  });

  it("do not count a comparison among them", () => {
    expect(isItemCondition({ op: "compare", cmp: "==", left: { field: "a" }, right: { literal: 1 } })).toBe(false);
  });
});

describe("the Simple view's condition rows", () => {
  it("show contains with its operands", () => {
    const [row] = conditionRows(TITLE_HAS_BUG);
    expect([row.left, row.op, row.right]).toEqual(["title", "contains", '"bug"']);
  });

  it("show host_suffix in words, the item as its field", () => {
    const [row] = conditionRows(HOST_IN_DOMAIN);
    expect([row.left, row.op, row.right]).toEqual(["its host", "is in the domain", '"example.com"']);
  });

  it("show any as one phrase, read-only words", () => {
    const [row] = conditionRows(ANY_LINK);
    expect([row.left, row.leftKind]).toEqual(['for at least one of links, its host is in the domain "example.com"', "words"]);
  });

  it("keep the stored node, so the row can be removed", () => {
    const [row] = conditionRows(ALL_TAGS);
    expect(row.node).toBe(ALL_TAGS);
  });
});

describe("the list's guard text", () => {
  it("says any/all in words", () => {
    expect(guardText(ANY_LINK)).toBe("for at least one of links, (its host is in the domain example.com)");
  });

  it("says contains in words", () => {
    expect(guardText(TITLE_HAS_BUG)).toBe("title contains bug");
  });
});

describe("the rules view", () => {
  it("says all in words, never its raw op", () => {
    expect(conditionText(ALL_TAGS).rest).toBe("for every one of tags, it contains word");
  });

  it("finds the variables an item condition reads, inside any/all too", () => {
    const rule = { condition: ALL_TAGS, action: { kind: "noop", params: {} } } as unknown as Rule;
    expect(upstreamVars(rule).sort((a, b) => a.localeCompare(b))).toEqual(["tags", "word"]);
  });
});
