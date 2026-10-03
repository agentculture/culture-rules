import type { MachineStatus } from "../api/statistics";
import type { Machine, Rule, RunSummary, Workflow } from "../api/types";
import { machineColors } from "../culture-design/chart";

export type Range = "1h" | "24h" | "7d";
export const RANGES: readonly Range[] = ["1h", "24h", "7d"];

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

export interface RangeSpec {
  /** The window's length in ms; it always ends at `now`. */
  span: number;
  buckets: number;
  bucketMs: number;
  /** "Runs per <unit>". */
  unit: string;
  /** "last <window>" in words, for the chart's accessible name. */
  window: string;
}

const SPECS: Record<Range, RangeSpec> = {
  "1h": { span: HOUR, buckets: 12, bucketMs: 5 * MINUTE, unit: "5 min", window: "last hour" },
  "24h": { span: DAY, buckets: 24, bucketMs: HOUR, unit: "hour", window: "last 24 hours" },
  "7d": { span: 7 * DAY, buckets: 7, bucketMs: DAY, unit: "day", window: "last 7 days" },
};

export const rangeSpec = (range: Range): RangeSpec => SPECS[range];

export interface LoadMeter {
  label: "CPU" | "GPU" | "Mem";
  /** Percent 0-100; null = not reported (rendered "n/a"). */
  value: number | null;
}

export interface Bucket {
  /** Start of the bucket, epoch ms. */
  start: number;
  count: number;
}

export interface Lane {
  name: string;
  /** Machine palette slot (culture-design/chart.ts); null = neutral (offline). */
  slot: number | null;
  online: boolean;
  statusText: string;
  load: LoadMeter[];
  running: { step: string; workflow: string }[];
  idleText: string;
  queue: number;
  buckets: Bucket[];
  ok: number;
  failed: number;
  /** True when load/queue/running were derived from runs, not read from a status endpoint. */
  derived: boolean;
}

export interface BuildInput {
  machines: Machine[];
  rules: Rule[];
  runs: RunSummary[];
  /** `GET /machines/status`, or null when it could not be read. */
  statuses: MachineStatus[] | null;
  range: Range;
  now: number;
  workflows?: Workflow[];
}

/** "35m", "2h", "3d" — how long ago, coarse on purpose. */
export function since(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const minutes = Math.max(0, Math.floor((now - t) / MINUTE));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return hours < 24 ? `${hours}h` : `${Math.floor(hours / 24)}d`;
}

/** Missed beats before a machine is offline (culture_rules/machines/heartbeat.py: 3 x 10 s). */
export const OFFLINE_AFTER_MS = 30_000;

/** Whether a last heartbeat is older than OFFLINE_AFTER_MS (unknown is not stale). */
export function stale(iso: string | null | undefined, now: number): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  return !Number.isNaN(t) && now - t > OFFLINE_AFTER_MS;
}

const LABELS = ["CPU", "GPU", "Mem"] as const;
const NO_LOAD: LoadMeter[] = LABELS.map((label) => ({ label, value: null }));

function loadOf(status: MachineStatus | undefined): LoadMeter[] {
  if (!status?.load) return NO_LOAD;
  const { cpu, gpu, mem } = status.load;
  return [
    { label: "CPU", value: cpu },
    { label: "GPU", value: gpu },
    { label: "Mem", value: mem },
  ];
}

/**
 * Whether a run belongs to a machine: the machines its steps ran on (`hosts`,
 * from the API); a run the API reports no hosts for falls back to its rule's
 * placement.
 */
function ranOn(run: RunSummary, machine: string, machineOfRule: Map<string, string | null>): boolean {
  if (run.hosts && run.hosts.length > 0) return run.hosts.includes(machine);
  return Boolean(run.rule_id) && machineOfRule.get(run.rule_id as string) === machine;
}

/** The window's activity buckets plus its ok / failed totals. */
function tally(runs: RunSummary[], spec: RangeSpec, now: number) {
  const start = now - spec.span;
  const buckets: Bucket[] = Array.from({ length: spec.buckets }, (_, i) => ({
    start: start + i * spec.bucketMs,
    count: 0,
  }));
  let ok = 0;
  let failed = 0;
  for (const run of runs) {
    const at = Date.parse(run.finished_at ?? run.created_at ?? "");
    if (Number.isNaN(at) || at < start || at >= now) continue;
    buckets[Math.min(spec.buckets - 1, Math.floor((at - start) / spec.bucketMs))].count += 1;
    if (run.status === "succeeded") ok += 1;
    else if (run.status === "failed") failed += 1;
  }
  return { buckets, ok, failed };
}

/** The lane's status line and its idle words. */
function statusWords(
  derived: boolean,
  online: boolean,
  status: MachineStatus | undefined,
  now: number,
): { statusText: string; idleText: string } {
  if (derived) {
    return { statusText: online ? "enrolled" : "disabled", idleText: online ? "idle" : "disabled" };
  }
  if (online) return { statusText: "online", idleText: "idle" };
  const ago = since(status?.last_seen, now);
  return { statusText: ago ? `offline ${ago}` : "offline", idleText: "not reachable" };
}

/**
 * What a lane shows running and queued: derived from its runs (`mine`, when the
 * status endpoint could not be read), else read from the machine's status.
 */
function work(
  mine: RunSummary[] | null,
  status: MachineStatus | undefined,
  online: boolean,
  workflowName: (id: string | null) => string,
): Pick<Lane, "running" | "queue"> {
  if (mine) {
    return {
      running: mine
        .filter((r) => r.status === "running")
        .map((r) => ({ step: "Running", workflow: workflowName(r.workflow_id) })),
      queue: mine.filter((r) => r.status === "pending").length,
    };
  }
  if (!online) return { running: [], queue: 0 };
  return { running: status?.running ?? [], queue: status?.queue_depth ?? 0 };
}

export function buildLanes(input: BuildInput): Lane[] {
  const { machines, rules, runs, statuses, range, now } = input;
  const spec = rangeSpec(range);
  const slots = machineColors(machines.map((m) => m.name));
  const machineOfRule = new Map(rules.map((r) => [r.id, r.placement?.machine ?? null]));
  const workflowName = (id: string | null) =>
    (id && input.workflows?.find((w) => w.id === id)?.name) || id || "workflow";
  const statusOf = new Map((statuses ?? []).map((s) => [s.name, s]));
  const derived = statuses === null;

  return machines.map((machine): Lane => {
    const mine = runs.filter((r) => ranOn(r, machine.name, machineOfRule));
    const status = statusOf.get(machine.name);
    // Machines the status endpoint does not list are reported as not reachable.
    // A machine the API last saw online turns offline here once its heartbeat is
    // stale (no beat for OFFLINE_AFTER_MS), even before the next answer says so.
    const online = derived
      ? machine.enabled !== false
      : status?.online === true && !stale(status.last_seen, now);
    const slot = online ? ((slots.get(machine.name) as number | undefined) ?? null) : null;
    const { running, queue } = work(derived ? mine : null, status, online, workflowName);
    const { statusText, idleText } = statusWords(derived, online, status, now);
    const { buckets, ok, failed } = tally(mine, spec, now);

    return {
      name: machine.name,
      slot,
      online,
      statusText,
      load: online ? loadOf(status) : NO_LOAD,
      running,
      idleText,
      queue,
      buckets,
      ok,
      failed,
      derived,
    };
  });
}

export const peak = (lane: Lane) => lane.buckets.reduce((m, b) => Math.max(m, b.count), 0);

/** "spark runs per hour, last 24 hours, peak 11" — the chart's accessible name. */
export const activityLabel = (lane: Lane, range: Range) =>
  `${lane.name} runs per ${rangeSpec(range).unit}, ${rangeSpec(range).window}, peak ${peak(lane)}`;

/** A bucket's time, for tooltips and the table: "14:00", or "Sat 3 Oct" for days. */
export function bucketLabel(start: number, range: Range): string {
  const d = new Date(start);
  if (range === "7d") {
    return d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
  }
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
