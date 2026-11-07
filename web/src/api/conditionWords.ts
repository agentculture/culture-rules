import type { Condition, ItemCondition, Operand } from "./types";

/**
 * Plain words for the condition ops the editor reads but does not build (`contains`,
 * `host_suffix`, `any`/`all`), the same words culture_rules/model/describe.py's
 * `condition_words` says: `title contains bug`, `its host is in the domain example.com`,
 * `for at least one of links, ...`. The condition editor shows them read-only; they are
 * edited as raw JSON.
 */

/** The current element of an `any`/`all`: `it` (the element itself) or `its <last segment>`. */
export function itemWords(path: string): string {
  if (path === ".") return "it";
  return `its ${path.split(".").pop() || path}`;
}

/** An `{item}` operand in words, or undefined for any other operand. */
export const itemOperandWords = (operand: Operand): string | undefined =>
  "item" in operand ? itemWords(operand.item) : undefined;

/** Whether `c` is one of the ops the editor only reads. */
export function isItemCondition(c: Condition): c is ItemCondition {
  return c.op === "contains" || c.op === "host_suffix" || c.op === "any" || c.op === "all";
}

/** `c` in words, its operands by `operand` and an `any`/`all`'s inner condition by `inner`. */
export function itemConditionWords(
  c: ItemCondition,
  operand: (o: Operand) => string,
  inner: (c: Condition) => string,
): string {
  switch (c.op) {
    case "contains":
      return `${operand(c.value)} contains ${operand(c.needle)}`;
    case "host_suffix":
      return `${operand(c.value)} is in the domain ${operand(c.suffix)}`;
    case "any":
      return `for at least one of ${operand(c.items)}, ${inner(c.where)}`;
    case "all":
      return `for every one of ${operand(c.items)}, ${inner(c.where)}`;
  }
}

/** The operands an item condition reads directly (not those inside an `any`/`all`'s `where`). */
export function itemConditionOperands(c: ItemCondition): Operand[] {
  switch (c.op) {
    case "contains":
      return [c.value, c.needle];
    case "host_suffix":
      return [c.value, c.suffix];
    case "any":
    case "all":
      return [c.items];
  }
}
