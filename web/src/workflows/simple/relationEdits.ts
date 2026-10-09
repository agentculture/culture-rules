import type { RuleDoc } from "../../api/rules";
import { canRelate, withRelation, withoutRelation, type Relation, type RelationKind } from "../../rules/relations";

interface RuleWrites {
  rules: RuleDoc[];
  save: (rule: RuleDoc) => Promise<boolean>;
  setNotice: (notice: string | null) => void;
}

/**
 * Relationship edits exactly as the Rules tab makes them (routes/Rules.tsx
 * `useRelationEdits`): each one is a PUT of the rule that declares it, built
 * with web/src/rules/relations.ts, and a refused one (a cycle, itself) is
 * named instead of sent.
 */
export function relationEdits({ rules, save, setNotice }: RuleWrites) {
  const relate = async (from: RuleDoc, kind: RelationKind, target: string) => {
    const why = canRelate(rules, from.id, kind, target);
    if (why) return setNotice(`${from.name}: ${why}`);
    await save(withRelation(from, kind, target));
  };
  const unrelate = async (rel: Relation) => {
    const from = rules.find((r) => r.id === rel.from);
    if (from) await save(withoutRelation(from, rel.kind, rel.to));
  };
  const moveRelation = async (rel: Relation, to: RelationKind) => {
    const from = rules.find((r) => r.id === rel.from);
    if (!from || rel.kind === to) return;
    const rest = { ...from, [rel.kind]: (from[rel.kind] ?? []).filter((t) => t !== rel.to) };
    const why = canRelate([rest, ...rules.filter((r) => r.id !== from.id)], from.id, to, rel.to);
    if (why) return setNotice(`${from.name}: ${why}`);
    await save(withRelation(rest, to, rel.to));
  };
  return { relate, unrelate, moveRelation };
}
