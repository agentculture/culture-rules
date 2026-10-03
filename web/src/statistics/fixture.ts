import type { Machine, Rule, RunSummary, Workflow } from "../api/types";
import type { MachineStatus } from "../api/statistics";

/**
 * The 'Chosen — Statistics' board (design canvas row 'Chosen', D-Statistics)
 * as API data: spark / thor / spark2 online, orin offline for two hours,
 * with the per-machine status (load, running steps, queue) in the
 * `GET /machines/status` shape.
 */
export const HOUR = 3_600_000;

export const STAT_MACHINES: Machine[] = [
  { name: "spark", platform: "linux-aarch64", roles: ["engine_node"], enabled: true },
  { name: "thor", platform: "linux-aarch64", capabilities: ["gpu"], roles: ["runner"], enabled: true },
  { name: "spark2", platform: "linux-aarch64", roles: ["runner"], enabled: true },
  { name: "orin", platform: "linux-aarch64", roles: ["runner"], enabled: true },
];

export const STAT_WORKFLOWS: Workflow[] = [
  { id: "review-pr", name: "Review PR" },
  { id: "triage", name: "Triage" },
  { id: "train-batch", name: "Train batch" },
];

export const STAT_RULES: Rule[] = STAT_MACHINES.map((m) => ({
  id: `rule-${m.name}`,
  name: `Rule on ${m.name}`,
  trigger: { kind: "event" },
  action: { kind: "code.run" },
  placement: { machine: m.name },
  enabled: true,
}));

/** Runs per hour, oldest first, 24 entries — straight from the board. */
export const HOURS: Record<string, number[]> = {
  spark: [2, 3, 1, 0, 0, 1, 2, 4, 6, 8, 9, 7, 6, 8, 10, 11, 9, 7, 6, 5, 4, 4, 3, 2],
  thor: [5, 6, 6, 7, 7, 6, 5, 5, 4, 6, 7, 8, 8, 9, 9, 10, 12, 11, 9, 8, 7, 6, 6, 5],
  spark2: [0, 0, 0, 0, 0, 0, 1, 2, 3, 3, 2, 1, 2, 3, 4, 3, 2, 1, 1, 1, 0, 0, 0, 0],
  orin: [2, 2, 1, 1, 2, 3, 2, 2, 1, 1, 2, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
};

/** Hours (index into HOURS) whose first run failed, per machine. */
const FAILED_HOURS: Record<string, number[]> = {
  spark: [9, 14, 20],
  thor: [2, 7, 12, 16, 22],
  spark2: [],
  orin: [5],
};

export function statRuns(now: number): RunSummary[] {
  const start = now - 24 * HOUR;
  const runs: RunSummary[] = [];
  for (const [machine, hours] of Object.entries(HOURS)) {
    hours.forEach((count, b) => {
      for (let j = 0; j < count; j++) {
        const at = start + b * HOUR + Math.floor(((j + 0.5) * HOUR) / count);
        runs.push({
          id: `run-${machine}-${b}-${j}`,
          status: j === 0 && FAILED_HOURS[machine].includes(b) ? "failed" : "succeeded",
          rule_id: `rule-${machine}`,
          workflow_id: "review-pr",
          started_by: "trigger",
          created_at: new Date(at - 60_000).toISOString(),
          finished_at: new Date(at).toISOString(),
        });
      }
    });
  }
  return runs;
}

export function statStatuses(now: number): MachineStatus[] {
  return [
    {
      name: "spark",
      online: true,
      last_seen: new Date(now - 5_000).toISOString(),
      load: { cpu: 38, gpu: 22, mem: 61 },
      running: [
        { step: "Fetch diff", workflow: "Review PR" },
        { step: "Decide", workflow: "Triage" },
      ],
      queue_depth: 2,
    },
    {
      name: "thor",
      online: true,
      last_seen: new Date(now - 4_000).toISOString(),
      load: { cpu: 71, gpu: 92, mem: 78 },
      running: [
        { step: "Review", workflow: "Review PR" },
        { step: "Train", workflow: "Train batch" },
      ],
      queue_depth: 4,
    },
    {
      name: "spark2",
      online: true,
      last_seen: new Date(now - 6_000).toISOString(),
      load: { cpu: 12, gpu: 4, mem: 30 },
      running: [],
      queue_depth: 0,
    },
    {
      name: "orin",
      online: false,
      last_seen: new Date(now - 2 * HOUR - 60_000).toISOString(),
      load: null,
      running: [],
      queue_depth: 0,
    },
  ];
}
