import { useState, type DragEvent } from "react";
import type { RuleDoc } from "../api/rules";
import {
  RELATION_KINDS,
  RELATION_LABEL,
  relationText,
  type Direction,
  type Relation,
  type RelationKind,
} from "./relations";

const MOVE_TYPE = "application/x-culture-relation";

interface SlotsProps {
  rules: RuleDoc[];
  focused: RuleDoc;
  /** The rule id being dragged out of the list, if any. */
  dragging: string | null;
  onAdd: (kind: RelationKind, target: string) => void;
  onMove: (rel: Relation, to: RelationKind) => void;
}

/**
 * Three slots, one per relationship kind. Each is a drop target (drag a rule
 * from the list, or an existing card from another slot) and, for the keyboard,
 * a native picker. Always present, so progressive disclosure stays in the `+`
 * and the list rather than in hidden controls.
 */
export function RelationSlots({ rules, focused, dragging, onAdd, onMove }: SlotsProps) {
  const [over, setOver] = useState<RelationKind | null>(null);
  const others = rules.filter((r) => r.id !== focused.id);

  const drop = (kind: RelationKind) => (e: DragEvent) => {
    e.preventDefault();
    setOver(null);
    const moved = e.dataTransfer.getData(MOVE_TYPE);
    if (moved) {
      try {
        onMove(JSON.parse(moved) as Relation, kind);
      } catch {
        /* not ours */
      }
      return;
    }
    const id = e.dataTransfer.getData("text/plain") || dragging;
    if (id) onAdd(kind, id);
  };

  return (
    <div className="relation-slots" role="group" aria-label="Relationships">
      {RELATION_KINDS.map((kind) => (
        <div
          key={kind}
          className={`relation-slot${over === kind ? " is-over" : ""}${dragging ? " is-armed" : ""}`}
          data-testid={`drop-${kind}`}
          onDragOver={(e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = "link";
            setOver(kind);
          }}
          onDragLeave={() => setOver((o) => (o === kind ? null : o))}
          onDrop={drop(kind)}
        >
          <select
            aria-label={`Add ${RELATION_LABEL[kind]}`}
            value=""
            onChange={(e) => e.target.value && onAdd(kind, e.target.value)}
          >
            <option value="">+ {RELATION_LABEL[kind]}…</option>
            {others.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>
        </div>
      ))}
    </div>
  );
}

interface CardProps {
  rel: Relation;
  direction: Direction;
  nameOf: (id: string) => string;
  /** The variables the upstream rule hands over (the board's `verdict` chip). */
  chip?: string;
  onRemove: (rel: Relation) => void;
}

/** A dashed relationship card: a badge for one end of a relationship, with its remove. */
export function RelationCard({ rel, direction, nameOf, chip, onRemove }: CardProps) {
  const text = relationText(rel, direction, nameOf);
  const other = direction === "out" ? rel.to : rel.from;
  return (
    <div
      className="relationship relationship--editable"
      data-testid="relationship"
      data-relation={rel.kind}
      data-direction={direction}
      draggable={direction === "out"}
      onDragStart={(e) => {
        e.dataTransfer.setData(MOVE_TYPE, JSON.stringify(rel));
        e.dataTransfer.effectAllowed = "link";
      }}
    >
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <path d="M4 4v7a4 4 0 0 0 4 4h12M16 11l4 4-4 4" />
      </svg>
      <span>
        {direction === "out" ? (
          <>
            {RELATION_LABEL[rel.kind]} <strong>{nameOf(other)}</strong>
          </>
        ) : rel.kind === "supersedes" ? (
          <>
            superseded by <strong>{nameOf(other)}</strong>
          </>
        ) : (
          <>
            <strong>{nameOf(other)}</strong> {RELATION_LABEL[rel.kind]} this
          </>
        )}
      </span>
      {chip ? <span className="chip chip--var">{chip}</span> : null}
      <button
        type="button"
        className="relationship__remove"
        aria-label={`Remove: ${text}`}
        onClick={() => onRemove(rel)}
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
          <path d="M6 6l12 12M18 6 6 18" />
        </svg>
      </button>
    </div>
  );
}
