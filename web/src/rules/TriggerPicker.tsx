import { useId, useState } from "react";
import type { Actor, AppActorParams, RunnerActorParams } from "../api/actors";
import type {
  ProbeMode,
  Trigger,
  TriggerKind,
  TypedTrigger,
} from "../api/types";

/** The kinds the picker offers, in the order a person thinks of them. */
const KINDS: { kind: TriggerKind; label: string }[] = [
  { kind: "event", label: "An app event" },
  { kind: "schedule", label: "On a schedule" },
  { kind: "probe", label: "A probe" },
  { kind: "manual", label: "Manually" },
];

/** Presets the schedule and probe forms offer; anything else is a custom cron. */
const PRESETS: { cron: string; label: string }[] = [
  { cron: "*/5 * * * *", label: "Every 5 minutes" },
  { cron: "0 * * * *", label: "Every hour" },
  { cron: "0 9 * * *", label: "Every day at 9:00" },
];
const CUSTOM = "__custom__";
const TIME_ZONES = [
  "UTC",
  "Europe/London",
  "America/New_York",
  "Asia/Jerusalem",
];

export function blankTrigger(kind: TriggerKind): Trigger {
  switch (kind) {
    case "event":
      return { kind, params: { type: "" } };
    case "schedule":
      return { kind, params: { cron: PRESETS[1].cron } };
    case "probe":
      return {
        kind,
        params: {
          actor: "",
          command: "",
          mode: "change",
          schedule: PRESETS[1].cron,
        },
      };
    default:
      return { kind: "manual" };
  }
}

/**
 * What stops a trigger from being saved, as a guidance code (api/guidance.ts),
 * or null when it is complete. A kind the editor does not know is kept as is.
 */
export function triggerProblem(
  trigger: Trigger,
): "trigger_type_required" | "invalid" | null {
  const t = trigger as TypedTrigger;
  if (t.kind === "event")
    return t.params?.type ? null : "trigger_type_required";
  if (t.kind === "schedule") return t.params?.cron?.trim() ? null : "invalid";
  if (t.kind === "probe") {
    const { actor, command, schedule } = t.params ?? {};
    return actor && command && schedule?.trim() ? null : "invalid";
  }
  return null;
}

const isApp = (a: Actor) => a.kind === "app" && a.enabled !== false;
const eventsOf = (a: Actor) =>
  (a.params as Partial<AppActorParams> | undefined)?.events ?? [];
const commandsOf = (a: Actor) =>
  Object.keys((a.params as RunnerActorParams | undefined)?.commands ?? {});

// A cron in words: the presets, every-N-minutes, else the expression itself.
export function cronWords(cron: string): string {
  const preset = PRESETS.find((p) => p.cron === cron.trim());
  if (preset) return preset.label;
  const every = /^\*\/(\d+) \* \* \* \*$/.exec(cron.trim());
  if (every) return `Every ${every[1]} minutes`;
  return cron.trim()
    ? `Custom schedule: ${cron.trim()}`
    : "Choose when it runs";
}

interface CronProps {
  label: string;
  cron: string;
  onChange: (cron: string) => void;
}

/** A preset select plus, for Custom, a cron field; always with the schedule in words. */
function CronField({ label, cron, onChange }: Readonly<CronProps>) {
  const name = useId();
  const [custom, setCustom] = useState(!PRESETS.some((p) => p.cron === cron));
  return (
    <>
      <label>
        <span id={`${name}-repeat`}>{label}</span>
        <select
          aria-labelledby={`${name}-repeat`}
          value={custom ? CUSTOM : cron}
          onChange={(e) => {
            const picked = e.target.value;
            setCustom(picked === CUSTOM);
            if (picked !== CUSTOM) onChange(picked);
          }}
        >
          {PRESETS.map((p) => (
            <option key={p.cron} value={p.cron}>
              {p.label}
            </option>
          ))}
          <option value={CUSTOM}>Custom</option>
        </select>
      </label>
      {custom ? (
        <label>
          <span id={`${name}-cron`}>Cron</span>
          <input
            aria-labelledby={`${name}-cron`}
            value={cron}
            onChange={(e) => onChange(e.target.value)}
            spellCheck={false}
          />
        </label>
      ) : null}
      <p className="trigger-picker__words" data-testid="cron-words">
        {cronWords(cron)}
      </p>
    </>
  );
}

const Empty = ({ children }: Readonly<{ children: string }>) => (
  <output className="trigger-picker__empty">{children}</output>
);

type EventTrigger = Extract<TypedTrigger, { kind: "event" }>;
type ScheduleTrigger = Extract<TypedTrigger, { kind: "schedule" }>;
type ProbeTrigger = Extract<TypedTrigger, { kind: "probe" }>;

interface FieldsProps<T> {
  name: string;
  t: T;
  onChange: (trigger: Trigger) => void;
}

/** Event trigger: the app (surface) that declares the event, then the event itself. */
function EventFields({ name, t, actors: apps, onChange }: Readonly<FieldsProps<EventTrigger> & { actors: Actor[] }>) {
  const eventType = t.params?.type ?? "";
  const owner = apps.find((a) => eventsOf(a).includes(eventType));
  const [surface, setSurface] = useState(owner?.id ?? "");
  const surfaceActor = apps.find((a) => a.id === surface);
  return (
    <>
      {apps.length === 0 ? (
        <Empty>
          No app has declared events yet. Add one on the Actors tab.
        </Empty>
      ) : null}
      <label>
        <span id={`${name}-surface`}>Surface</span>
        <select
          aria-labelledby={`${name}-surface`}
          value={surface}
          onChange={(e) => {
            setSurface(e.target.value);
            onChange({ kind: "event", params: { ...t.params, type: "" } });
          }}
        >
          <option value="">Choose an app…</option>
          {apps.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span id={`${name}-event`}>Event</span>
        <select
          aria-labelledby={`${name}-event`}
          value={eventType}
          disabled={!surfaceActor}
          onChange={(e) =>
            onChange({
              kind: "event",
              params: { ...t.params, type: e.target.value },
            })
          }
        >
          <option value="">Choose an event…</option>
          {eventType &&
          surfaceActor &&
          !eventsOf(surfaceActor).includes(eventType) ? (
            <option value={eventType}>
              {eventType} (no longer declared)
            </option>
          ) : null}
          {(surfaceActor ? eventsOf(surfaceActor) : []).map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

/** Schedule trigger: a cron and an optional time zone. */
function ScheduleFields({ name, t, onChange }: Readonly<FieldsProps<ScheduleTrigger>>) {
  return (
    <>
      <CronField
        label="Repeat"
        cron={t.params.cron ?? ""}
        onChange={(cron) =>
          onChange({ kind: "schedule", params: { ...t.params, cron } })
        }
      />
      <label>
        <span id={`${name}-timezone`}>Time zone</span>
        <input
          aria-labelledby={`${name}-timezone`}
          list={`${name}-tz`}
          value={t.params.tz ?? ""}
          placeholder="Optional, e.g. UTC"
          onChange={(e) => {
            const { tz: _drop, ...rest } = t.params;
            onChange({
              kind: "schedule",
              params: e.target.value
                ? { ...rest, tz: e.target.value }
                : rest,
            });
          }}
        />
        <datalist id={`${name}-tz`}>
          {TIME_ZONES.map((z) => (
            <option key={z} value={z} />
          ))}
        </datalist>
      </label>
    </>
  );
}

/** Probe trigger: a runner's registered command on a cron, firing on change or on a condition. */
function ProbeFields({ name, t, actors: runners, onChange }: Readonly<FieldsProps<ProbeTrigger> & { actors: Actor[] }>) {
  const runner = runners.find((a) => a.id === (t.params.actor ?? ""));
  return (
    <>
      {runners.length === 0 ? (
        <Empty>
          No runner has registered a command yet. Add one on the Actors tab.
        </Empty>
      ) : null}
      <label>
        <span id={`${name}-actor`}>Actor</span>
        <select
          aria-labelledby={`${name}-actor`}
          value={t.params.actor ?? ""}
          onChange={(e) =>
            onChange({
              kind: "probe",
              params: { ...t.params, actor: e.target.value, command: "" },
            })
          }
        >
          <option value="">Choose a runner…</option>
          {runners.map((a) => (
            <option key={a.id} value={a.id}>
              {a.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span id={`${name}-command`}>Command</span>
        <select
          aria-labelledby={`${name}-command`}
          value={t.params.command ?? ""}
          disabled={!(t.params.actor ?? "")}
          onChange={(e) =>
            onChange({
              kind: "probe",
              params: { ...t.params, command: e.target.value },
            })
          }
        >
          <option value="">Choose a command…</option>
          {(runner ? commandsOf(runner) : []).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span id={`${name}-mode`}>Mode</span>
        <select
          aria-labelledby={`${name}-mode`}
          value={t.params.mode ?? "change"}
          onChange={(e) =>
            onChange({
              kind: "probe",
              params: { ...t.params, mode: e.target.value as ProbeMode },
            })
          }
        >
          <option value="change">Fire when the result changes</option>
          <option value="condition">Fire when the condition holds</option>
        </select>
      </label>
      <CronField
        label="Repeat"
        cron={t.params.schedule ?? ""}
        onChange={(schedule) =>
          onChange({ kind: "probe", params: { ...t.params, schedule } })
        }
      />
    </>
  );
}

interface Props {
  value: Trigger;
  actors: Actor[];
  onChange: (trigger: Trigger) => void;
}

/**
 * "When does this happen?" as typed choices: an event type is picked from what
 * an enabled app actor declares (never typed), a schedule is a cron plus
 * optional time zone, a probe is a runner's registered command on a cron.
 * Controlled: `value` is the trigger that would be saved.
 */
export default function TriggerPicker({
  value,
  actors,
  onChange,
}: Readonly<Props>) {
  const name = useId();
  const apps = actors.filter((a) => isApp(a) && eventsOf(a).length > 0);
  const runners = actors.filter(
    (a) =>
      a.kind === "runner" && a.enabled !== false && commandsOf(a).length > 0,
  );
  const known = KINDS.some((k) => k.kind === value.kind);
  // Narrowed on `kind`; a legacy rule's params may be label-only, so every read has a default.
  const t = value as TypedTrigger;

  const chooseKind = (kind: TriggerKind) => {
    if (kind !== value.kind) onChange(blankTrigger(kind));
  };

  return (
    <fieldset className="trigger-picker">
      <legend>When does this happen?</legend>
      <div className="trigger-picker__kinds">
        {KINDS.map(({ kind, label }) => (
          <label key={kind} className="trigger-kind">
            <input
              type="radio"
              name={name}
              checked={value.kind === kind}
              onChange={() => chooseKind(kind)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>
      {known ? null : (
        <p className="trigger-picker__words">
          This rule uses a “{value.kind}” trigger the editor cannot change; it
          is kept as it is.
        </p>
      )}

      {t.kind === "event" ? <EventFields name={name} t={t} actors={apps} onChange={onChange} /> : null}

      {t.kind === "schedule" ? <ScheduleFields name={name} t={t} onChange={onChange} /> : null}

      {t.kind === "probe" ? <ProbeFields name={name} t={t} actors={runners} onChange={onChange} /> : null}

      {t.kind === "manual" ? (
        <p className="trigger-picker__words">
          Runs only when someone starts it.
        </p>
      ) : null}
      <p className="trigger-picker__hint">
        To run only when something is true, add a condition afterwards with +
        Add stage.
      </p>
    </fieldset>
  );
}
