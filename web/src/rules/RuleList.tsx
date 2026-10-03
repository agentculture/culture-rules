import { Link } from "react-router-dom";
import type { RuleDoc } from "../api/rules";
import { MachineDot, Switch, machineStyle } from "../culture-design/stages";
import { badgesFor } from "./relations";

interface Props {
  rules: RuleDoc[];
  selectedId: string | null;
  slotOf: (rule: RuleDoc) => number | null;
  onToggle: (rule: RuleDoc) => void;
  onNew: () => void;
  onDragRule: (id: string | null) => void;
}

/**
 * The left column of the 'Chosen — Rules' board: the "New rule" button
 * (which opens the "When does this happen?" form), then one row per rule (machine dot, name, enable switch). A row
 * is also a drag handle: dropped on a relationship slot it relates the two
 * rules. While a rule is focused, the other end of each of its relationships
 * wears a badge on its row.
 */
export function RuleList({ rules, selectedId, slotOf, onToggle, onNew, onDragRule }: Readonly<Props>) {
  return (
    <nav className="rule-list" aria-label="Rules">
      <button type="button" className="rule-list__new" onClick={onNew}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
          <path d="M12 5v14M5 12h14" />
        </svg>
        New rule
      </button>
      {rules.map((rule) => {
        const enabled = rule.enabled !== false;
        const isSelected = rule.id === selectedId;
        const badges = selectedId && !isSelected ? badgesFor(rules, selectedId, rule.id) : [];
        return (
          <div
            key={rule.id}
            className={`rule-row${isSelected ? " is-selected" : ""}${enabled ? "" : " is-disabled"}`}
            data-rule-id={rule.id}
            style={machineStyle(slotOf(rule))}
            draggable
            onDragStart={(e) => {
              e.dataTransfer.setData("text/plain", rule.id);
              e.dataTransfer.effectAllowed = "link";
              onDragRule(rule.id);
            }}
            onDragEnd={() => onDragRule(null)}
          >
            <MachineDot slot={slotOf(rule)} />
            <div className="rule-row__text">
              <Link
                className="rule-row__name"
                to={`/rules/${encodeURIComponent(rule.id)}`}
                aria-current={isSelected ? "true" : undefined}
                draggable={false}
              >
                {rule.name}
              </Link>
              {badges.map((b) => (
                <span
                  key={`${b.kind}-${b.direction}`}
                  className="rel-badge"
                  data-testid="row-badge"
                  data-relation={b.kind}
                  data-direction={b.direction}
                >
                  {b.text}
                </span>
              ))}
            </div>
            <Switch
              label={`${rule.name} enabled`}
              checked={enabled}
              onChange={() => onToggle(rule)}
            />
          </div>
        );
      })}
    </nav>
  );
}
