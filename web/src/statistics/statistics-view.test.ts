import { describe, expect, it } from "vitest";
import { HOURS, STAT_MACHINES, STAT_RULES, statRuns, statStatuses } from "./fixture";
import { buildLanes, rangeSpec } from "./statistics-view";

const NOW = Date.parse("2026-10-03T12:00:00Z");

const lanes = (range: "1h" | "24h" | "7d" = "24h", statuses = statStatuses(NOW)) =>
  buildLanes({
    machines: STAT_MACHINES,
    rules: STAT_RULES,
    runs: statRuns(NOW),
    statuses,
    range,
    now: NOW,
  });

describe("rangeSpec", () => {
  it("1h is twelve 5-minute buckets, 24h twenty-four hourly, 7d seven daily", () => {
    expect(rangeSpec("1h")).toMatchObject({ buckets: 12, unit: "5 min" });
    expect(rangeSpec("24h")).toMatchObject({ buckets: 24, unit: "hour" });
    expect(rangeSpec("7d")).toMatchObject({ buckets: 7, unit: "day" });
  });
});

describe("buildLanes", () => {
  it("makes one lane per enrolled machine, offline ones included, in API order", () => {
    expect(lanes().map((l) => l.name)).toEqual(["spark", "thor", "spark2", "orin"]);
  });

  it("24h buckets are the runs per hour, oldest first", () => {
    const l = lanes();
    for (const machine of l) {
      expect(machine.buckets.map((b) => b.count)).toEqual(HOURS[machine.name]);
    }
  });

  it("counts ok and failed inside the range", () => {
    const spark = lanes()[0];
    const total = HOURS.spark.reduce((a, b) => a + b, 0);
    expect(spark.failed).toBe(3);
    expect(spark.ok).toBe(total - 3);
    // 7d sees the same 24 h of runs.
    expect(lanes("7d")[0]).toMatchObject({ failed: 3, ok: total - 3 });
    // 1h only sees the last hour's bucket.
    const hour = lanes("1h")[0];
    expect(hour.ok + hour.failed).toBe(HOURS.spark[23]);
    expect(hour.buckets).toHaveLength(12);
  });

  it("carries load, running steps and queue depth from the machine status", () => {
    const [spark, thor, spark2] = lanes();
    expect(spark.load).toEqual([
      { label: "CPU", value: 38 },
      { label: "GPU", value: 22 },
      { label: "Mem", value: 61 },
    ]);
    expect(spark.running).toEqual([
      { step: "Fetch diff", workflow: "Review PR" },
      { step: "Decide", workflow: "Triage" },
    ]);
    expect(thor.queue).toBe(4);
    expect(spark2.running).toEqual([]);
    expect(spark2.idleText).toBe("idle");
  });

  it("renders an offline machine offline: neutral color, no load, not reachable, since when", () => {
    const orin = lanes()[3];
    expect(orin).toMatchObject({
      online: false,
      slot: null,
      statusText: "offline 2h",
      idleText: "not reachable",
    });
    expect(orin.load.map((x) => x.value)).toEqual([null, null, null]);
    // history still shows: it ran 21 jobs before it dropped
    expect(orin.ok + orin.failed).toBe(HOURS.orin.reduce((a, b) => a + b, 0));
  });

  it("keeps each online machine on its palette slot, in machine order", () => {
    expect(lanes().map((l) => l.slot)).toEqual([0, 1, 2, null]);
  });

  it("without a status endpoint, derives running/queue from runs and marks lanes derived", () => {
    const runs = [
      ...statRuns(NOW),
      { id: "x1", status: "running", rule_id: "rule-thor", workflow_id: "train-batch", started_by: "t", created_at: new Date(NOW - 60_000).toISOString(), finished_at: null },
      { id: "x2", status: "pending", rule_id: "rule-thor", workflow_id: "train-batch", started_by: "t", created_at: new Date(NOW - 30_000).toISOString(), finished_at: null },
    ];
    const l = buildLanes({
      machines: [...STAT_MACHINES.slice(0, 2), { ...STAT_MACHINES[3], enabled: false }],
      rules: STAT_RULES,
      runs,
      statuses: null,
      range: "24h",
      now: NOW,
      workflows: [{ id: "train-batch", name: "Train batch" }],
    });
    const thor = l[1];
    expect(thor.derived).toBe(true);
    expect(thor.running).toEqual([{ step: "Running", workflow: "Train batch" }]);
    expect(thor.queue).toBe(1);
    expect(thor.load.map((x) => x.value)).toEqual([null, null, null]);
    expect(thor.online).toBe(true);
    expect(l[2]).toMatchObject({ online: false, statusText: "disabled", idleText: "disabled" });
  });
});
