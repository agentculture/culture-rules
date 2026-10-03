// culture-design/stages.tsx
//
// The rule-flow vocabulary of the design canvas ('Chosen — Rules' board,
// https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi): one shape per stage so
// a stage reads by silhouette before its words are parsed.
//
//   Trigger   — filled ink slab, lightning icon        (it fires)
//   Condition — white, DASHED border, diamond icon     (it may not pass)
//   Workflow  — white, solid ink border + offset shadow (the reusable work)
//   Action    — muted pill, arrow-out icon             (the side effect)
//
// Relationships to other rules are a dashed card above the flow, never a
// stage. Shapes live in styles/stages.css; colors come from tokens.css and
// styles/app.css only.

import type { CSSProperties, ReactNode } from "react";

export type StageKind = "trigger" | "condition" | "workflow" | "action";

const ICONS: Record<StageKind, ReactNode> = {
  trigger: <path d="M13 2 3 14h9l-1 8 10-12h-9l1-8z" strokeLinejoin="round" />,
  condition: <path d="M12 2 22 12 12 22 2 12z" />,
  workflow: (
    <>
      <circle cx="5" cy="6" r="3" />
      <circle cx="19" cy="18" r="3" />
      <path d="M8 6h5a3 3 0 0 1 3 3v6" />
    </>
  ),
  action: <path d="M7 17 17 7M8 7h9v9" />,
};

export function StageIcon({ kind, size = 24 }: Readonly<{ kind: StageKind; size?: number }>) {
  return (
    <svg
      className="stage__icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      aria-hidden="true"
    >
      {ICONS[kind]}
    </svg>
  );
}

export interface StageProps {
  kind: StageKind;
  /** The stage's main words (26px). */
  label: ReactNode;
  /** Mapping chips (`sha → commit`), mono. */
  chips?: string[];
}

export function Stage({ kind, label, chips = [] }: Readonly<StageProps>) {
  return (
    <div className={`stage stage--${kind}`} data-testid={`stage-${kind}`} data-stage={kind}>
      <div className="stage__row">
        <StageIcon kind={kind} size={kind === "trigger" ? 28 : 24} />
        <span className="stage__label">{label}</span>
        {kind === "action" && chips.length > 0 ? (
          <span className="stage__chips stage__chips--inline">
            {chips.map((c) => (
              <span className="chip chip--mapping" key={c}>
                {c}
              </span>
            ))}
          </span>
        ) : null}
      </div>
      {kind !== "action" && chips.length > 0 ? (
        <div className="stage__chips">
          {chips.map((c) => (
            <span className="chip chip--mapping" key={c}>
              {c}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/** The down-arrow between stages. */
export function StageArrow() {
  return (
    <svg className="stage-arrow" width="24" height="30" viewBox="0 0 24 30" aria-hidden="true">
      <path d="M12 0v24M6 19l6 6 6-6" fill="none" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  );
}

/** A relationship to another rule: a dashed card above the flow, not a stage. */
export function RelationshipCard({ children, chip }: Readonly<{ children: ReactNode; chip?: string }>) {
  return (
    <div className="relationship" data-testid="relationship">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
        <path d="M4 4v7a4 4 0 0 0 4 4h12M16 11l4 4-4 4" />
      </svg>
      <span>{children}</span>
      {chip ? <span className="chip chip--var">{chip}</span> : null}
    </div>
  );
}

/** The `+` that grows a rule by one stage (progressive disclosure). */
export function AddStageButton({ onClick }: Readonly<{ onClick?: () => void }>) {
  return (
    <button type="button" className="add-stage" aria-label="Add stage" onClick={onClick}>
      +
    </button>
  );
}

/** A machine's color, by palette slot (culture-design/chart.ts); null = neutral. */
export function machineStyle(slot: number | null): CSSProperties {
  return {
    ["--m" as string]: slot === null ? "var(--machine-none)" : `var(--machine-${slot})`,
    ["--m-tint" as string]:
      slot === null ? "var(--machine-none-tint)" : `var(--machine-${slot}-tint)`,
  } as CSSProperties;
}

export function MachineDot({ slot, size = 10 }: Readonly<{ slot: number | null; size?: number }>) {
  return (
    <span
      className="machine-dot"
      data-machine-slot={slot ?? "none"}
      style={{ ...machineStyle(slot), width: size, height: size }}
      aria-hidden="true"
    />
  );
}

export interface SwitchProps {
  label: string;
  checked: boolean;
  onChange?: (next: boolean) => void;
}

/** A large on/off switch (40×24), role="switch". */
export function Switch({ label, checked, onChange }: Readonly<SwitchProps>) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="switch"
      onClick={() => onChange?.(!checked)}
    >
      <span className="switch__knob" />
    </button>
  );
}
