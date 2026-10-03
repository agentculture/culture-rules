import type { RuleDoc } from "../api/rules";

/**
 * The three relationships between rules (issue #2): a rule *must run after*
 * another, *may run after* it (its outputs are visible if it ran), or
 * *supersedes* it (the other does not fire for an event this rule matched).
 * They are fields of the declaring rule, so both ends of one relationship
 * live in one document and every edit is a PUT of that rule.
 */
export type RelationKind = "must_after" | "may_after" | "supersedes";

export const RELATION_KINDS: RelationKind[] = ["must_after", "may_after", "supersedes"];

/** The slot labels: the same words as the flow's relationship cards. */
export const RELATION_LABEL: Record<RelationKind, string> = {
  must_after: "must run after",
  may_after: "may run after",
  supersedes: "supersedes",
};

export interface Relation {
  kind: RelationKind;
  /** The declaring rule. */
  from: string;
  /** The rule it points at. */
  to: string;
}

export type Direction = "out" | "in";

const targets = (rule: RuleDoc, kind: RelationKind): string[] => rule[kind] ?? [];

export function relationsOf(rules: RuleDoc[], id: string) {
  const outgoing: Relation[] = [];
  const incoming: Relation[] = [];
  for (const rule of rules) {
    for (const kind of RELATION_KINDS) {
      for (const to of targets(rule, kind)) {
        if (rule.id === id) outgoing.push({ kind, from: rule.id, to });
        else if (to === id) incoming.push({ kind, from: rule.id, to });
      }
    }
  }
  return { outgoing, incoming };
}

export function withRelation(rule: RuleDoc, kind: RelationKind, target: string): RuleDoc {
  const current = targets(rule, kind);
  return current.includes(target) ? rule : { ...rule, [kind]: [...current, target] };
}

export function withoutRelation(rule: RuleDoc, kind: RelationKind, target: string): RuleDoc {
  return { ...rule, [kind]: targets(rule, kind).filter((t) => t !== target) };
}

/** Whether `from` is reachable from `start` along `kind` edges (a cycle if we add from->start). */
function reaches(rules: RuleDoc[], kind: RelationKind, start: string, goal: string): boolean {
  const seen = new Set<string>();
  const stack = [start];
  while (stack.length > 0) {
    const id = stack.pop() as string;
    if (id === goal) return true;
    if (seen.has(id)) continue;
    seen.add(id);
    const rule = rules.find((r) => r.id === id);
    if (rule) stack.push(...targets(rule, kind));
  }
  return false;
}

/** Why `from kind to` may not be added, or null when it may. */
export function canRelate(
  rules: RuleDoc[],
  from: string,
  kind: RelationKind,
  to: string,
): string | null {
  if (from === to) return "a rule cannot be related to itself";
  const source = rules.find((r) => r.id === from);
  if (!source || !rules.some((r) => r.id === to)) return `unknown rule ${source ? to : from}`;
  if (targets(source, kind).includes(to)) return `${RELATION_LABEL[kind]} is already set`;
  if (kind !== "may_after" && reaches(rules, kind, to, from)) {
    return `that would make a cycle: ${to} already ${RELATION_LABEL[kind]} ${from}, directly or through other rules`;
  }
  return null;
}

/** One end's words. `out`: seen from the declaring rule; `in`: from the rule it points at. */
export function relationText(
  rel: Relation,
  direction: Direction,
  nameOf: (id: string) => string,
): string {
  if (direction === "out") return `${RELATION_LABEL[rel.kind]} ${nameOf(rel.to)}`;
  if (rel.kind === "supersedes") return `superseded by ${nameOf(rel.from)}`;
  return `${nameOf(rel.from)} ${RELATION_LABEL[rel.kind]} this`;
}

export interface RowBadge {
  kind: RelationKind;
  /** `in`: the row's rule is the one pointed at; `out`: the row's rule declares it. */
  direction: Direction;
  text: string;
}

const IN_WORDS: Record<RelationKind, string> = {
  must_after: "must run first",
  may_after: "may run first",
  supersedes: "superseded",
};
const OUT_WORDS: Record<RelationKind, string> = {
  must_after: "waits for this",
  may_after: "may follow",
  supersedes: "supersedes this",
};

/** The other end of the focused rule's relationships, as the list row's badges. */
export function badgesFor(rules: RuleDoc[], focusedId: string, rowId: string): RowBadge[] {
  const { outgoing, incoming } = relationsOf(rules, focusedId);
  return [
    ...outgoing
      .filter((r) => r.to === rowId)
      .map((r) => ({ kind: r.kind, direction: "in" as const, text: IN_WORDS[r.kind] })),
    ...incoming
      .filter((r) => r.from === rowId)
      .map((r) => ({ kind: r.kind, direction: "out" as const, text: OUT_WORDS[r.kind] })),
  ];
}
