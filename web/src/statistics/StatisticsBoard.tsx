import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useLiveUpdates } from "../api/live";
import { getMachineStatuses, type MachineStatus } from "../api/statistics";
import { ApiError, listMachines, listRules, listRuns, listWorkflows } from "../api/client";
import { settleAll } from "../api/settle";
import type { Machine, Rule, RunSummary, Workflow } from "../api/types";
import { setAgentState } from "../agent-state/store";
import { machineStyle } from "../culture-design/stages";
import { useTabReady } from "../routes/useTabReady";
import {
  RANGES,
  activityLabel,
  bucketLabel,
  buildLanes,
  peak,
  rangeSpec,
  type Lane,
  type Range,
} from "./statistics-view";
import "./statistics.css";

interface Loaded {
  machines: Machine[];
  rules: Rule[];
  workflows: Workflow[];
  runs: RunSummary[];
  /** null = `GET /machines/status` failed; lanes are derived from runs. */
  statuses: MachineStatus[] | null;
  errors: string[];
}

/** How often `GET /machines/status` is re-read (heartbeats beat every 10 s). */
export const STATUS_POLL_MS = 10_000;
const LIVE_COLLECTIONS = ["machines", "runs", "heartbeats"] as const;

const describe = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

function CheckIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m5 12 5 5 9-10" />
    </svg>
  );
}

function CrossIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" aria-hidden="true">
      <path d="M6 6l12 12M18 6 6 18" />
    </svg>
  );
}

function Outcome({ ok, failed }: Readonly<{ ok: number; failed: number }>) {
  return (
    <div className="lane__outcome">
      <span className="outcome outcome--ok">
        <CheckIcon />
        <strong>{ok}</strong> <span>ok</span>
      </span>
      <span className="outcome outcome--failed">
        <CrossIcon />
        <strong>{failed}</strong> <span>failed</span>
      </span>
    </div>
  );
}

/** Roving radio movement: +1 for Right/Down, -1 for Left/Up, 0 for any other key. */
function arrowStep(key: string): number {
  if (key === "ArrowRight" || key === "ArrowDown") return 1;
  if (key === "ArrowLeft" || key === "ArrowUp") return -1;
  return 0;
}

function RangeControl({ range, onChange }: Readonly<{ range: Range; onChange: (r: Range) => void }>) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>, i: number) => {
    const step = arrowStep(e.key);
    if (!step) return;
    e.preventDefault();
    const next = RANGES[(i + step + RANGES.length) % RANGES.length];
    onChange(next);
    refs.current[next]?.focus();
  };
  return (
    <div role="radiogroup" aria-label="Time range" className="range">
      {RANGES.map((r, i) => (
        <button
          key={r}
          ref={(el) => {
            refs.current[r] = el;
          }}
          type="button"
          role="radio"
          aria-checked={r === range}
          tabIndex={r === range ? 0 : -1}
          className="range__option"
          onClick={() => onChange(r)}
          onKeyDown={(e) => onKeyDown(e, i)}
        >
          {r}
        </button>
      ))}
    </div>
  );
}

function Loads({ lane }: Readonly<{ lane: Lane }>) {
  return (
    <div className="lane__load">
      {lane.load.map((l) => (
        <div key={l.label} className="meter">
          <span className="meter__label">{l.label}</span>
          <span className="meter__track">
            <span className="meter__fill" style={{ width: `${l.value ?? 0}%` }} />
          </span>
          <span className="meter__value">{l.value === null ? "n/a" : `${l.value}%`}</span>
        </div>
      ))}
    </div>
  );
}

function Working({ lane }: Readonly<{ lane: Lane }>) {
  return (
    <div className="lane__now">
      {lane.running.map((r, i) => (
        <span key={`${r.step}-${r.workflow}-${i}`} className="now-chip">
          <strong>{r.step}</strong>
          <span>{r.workflow}</span>
        </span>
      ))}
      {lane.running.length === 0 ? <span className="lane__idle">{lane.idleText}</span> : null}
      {lane.queue > 0 ? <span className="lane__queue">+{lane.queue} queued</span> : null}
    </div>
  );
}

const RANGE_UNIT_LABEL = (range: Range) => `Runs per ${rangeSpec(range).unit}`;

function Bars({ lane, range, scale }: Readonly<{ lane: Lane; range: Range; scale: number }>) {
  const [active, setActive] = useState<number | null>(null);
  const n = lane.buckets.length;
  const tip = active === null ? null : lane.buckets[active];
  return (
    <div className="lane__chart" onMouseLeave={() => setActive(null)}>
      <div className="bars" role="img" aria-label={activityLabel(lane, range)} data-bars={n}>
        {lane.buckets.map((b, i) => (
          <span
            key={b.start}
            data-bar=""
            data-count={b.count}
            className={`bar${b.count === 0 ? " bar--empty" : ""}${active === i ? " is-active" : ""}`}
            style={{ height: Math.max(2, Math.round((b.count / scale) * 56)) }}
            onMouseEnter={() => setActive(i)}
          />
        ))}
      </div>
      {tip && active !== null ? (
        <div role="tooltip" className="bar-tip" style={{ left: `${((active + 0.5) / n) * 100}%` }}>
          <strong>
            {tip.count} {tip.count === 1 ? "run" : "runs"}
          </strong>
          <span>{bucketLabel(tip.start, range)}</span>
        </div>
      ) : null}
    </div>
  );
}

function LaneRow({ lane, range, scale }: Readonly<{ lane: Lane; range: Range; scale: number }>) {
  return (
    <section
      aria-label={lane.name}
      className={`lane${lane.online ? "" : " lane--offline"}`}
      data-online={lane.online}
      data-machine-slot={lane.slot ?? "none"}
      style={machineStyle(lane.slot)}
    >
      <div className="lane__id">
        <span className="lane__name">{lane.name}</span>
        <span className="lane__status">
          <span className={`status-dot${lane.online ? " status-dot--on" : ""}`} aria-hidden="true" />
          {lane.statusText}
        </span>
      </div>
      <Loads lane={lane} />
      <Working lane={lane} />
      <Bars lane={lane} range={range} scale={scale} />
      <Outcome ok={lane.ok} failed={lane.failed} />
    </section>
  );
}

function Tables({ lanes, range }: Readonly<{ lanes: Lane[]; range: Range }>) {
  const buckets = lanes[0]?.buckets ?? [];
  return (
    <div className="stats-tables">
      <table className="stats-table" aria-label="Machines">
        <thead>
          <tr>
            {["Machine", "Status", "CPU", "GPU", "Mem", "Working on", "Queue", "Succeeded", "Failed"].map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {lanes.map((l) => (
            <tr key={l.name}>
              <th scope="row">{l.name}</th>
              <td>{l.statusText}</td>
              {l.load.map((m) => (
                <td key={m.label}>{m.value === null ? "n/a" : `${m.value}%`}</td>
              ))}
              <td>
                {l.running.length > 0
                  ? l.running.map((r) => `${r.step} (${r.workflow})`).join(", ")
                  : l.idleText}
              </td>
              <td>{l.queue > 0 ? `+${l.queue} queued` : "none"}</td>
              <td>
                <span className="outcome outcome--ok">
                  <CheckIcon />
                  <strong>{l.ok}</strong> <span>ok</span>
                </span>
              </td>
              <td>
                <span className="outcome outcome--failed">
                  <CrossIcon />
                  <strong>{l.failed}</strong> <span>failed</span>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <table className="stats-table" aria-label={RANGE_UNIT_LABEL(range)}>
        <thead>
          <tr>
            <th scope="col">{rangeSpec(range).window.replace(/^last /, "")}</th>
            {lanes.map((l) => (
              <th key={l.name} scope="col">
                {l.name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {buckets.map((b, i) => (
            <tr key={b.start}>
              <th scope="row">{bucketLabel(b.start, range)}</th>
              {lanes.map((l) => (
                <td key={l.name}>{l.buckets[i].count}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/**
 * The Statistics tab — the 'Chosen — Statistics' board (design canvas row
 * 'Chosen'): one lane per enrolled machine, offline ones included, each
 * with load, what it is working on, queue depth, runs per time bucket and
 * ok/failed. Controls: time range 1h/24h/7d and a table view.
 */
export function StatisticsBoard() {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [range, setRange] = useState<Range>("24h");
  const [view, setView] = useState<"lanes" | "table">("lanes");

  // First load, then a full refetch whenever the live feed reports a write to
  // machines, runs or heartbeats (useLiveUpdates), plus a gentle poll of
  // `GET /machines/status`: heartbeats change load without a write this tab
  // would otherwise see, and a machine whose heartbeat stops must turn offline.
  const [reload, setReload] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    settleAll(
      [
        listMachines(signal),
        listRules(signal),
        listWorkflows(signal),
        listRuns({ limit: 1000 }, signal),
        getMachineStatuses(signal),
      ],
      ([machines, rules, workflows, runs, statuses]) => {
        if (signal.aborted) return;
        const errors = [machines, rules, workflows, runs, statuses]
          .filter((r): r is PromiseRejectedResult => r.status === "rejected")
          .map((r) => describe(r.reason));
        setNow(Date.now());
        setLoaded({
          machines: machines.status === "fulfilled" ? machines.value : [],
          rules: rules.status === "fulfilled" ? rules.value : [],
          workflows: workflows.status === "fulfilled" ? workflows.value : [],
          runs: runs.status === "fulfilled" ? runs.value : [],
          statuses: statuses.status === "fulfilled" ? statuses.value : null,
          errors,
        });
      },
      (message) => {
        // Applying the load failed: show an empty board with the failure named.
        if (signal.aborted) return;
        setNow(Date.now());
        setLoaded({ machines: [], rules: [], workflows: [], runs: [], statuses: null, errors: [message] });
      },
    );
    return () => controller.abort();
  }, [reload]);

  const refetch = useCallback(() => setReload((n) => n + 1), []);
  const live = useLiveUpdates(LIVE_COLLECTIONS, refetch);

  useEffect(() => {
    const controller = new AbortController();
    const timer = setInterval(() => {
      setNow(Date.now());
      getMachineStatuses(controller.signal)
        .then((statuses) => {
          if (controller.signal.aborted) return;
          setLoaded((l) => (l ? { ...l, statuses } : l));
        })
        .catch(() => {
          // Keep the last answer: staleness (last_seen) turns a silent machine offline.
        });
    }, STATUS_POLL_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  const lanes = useMemo(
    () =>
      loaded
        ? buildLanes({
            machines: loaded.machines,
            rules: loaded.rules,
            runs: loaded.runs,
            statuses: loaded.statuses,
            workflows: loaded.workflows,
            range,
            now,
          })
        : [],
    [loaded, range, now],
  );
  // One axis: every lane's bars share the busiest bucket as 100%.
  const scale = Math.max(1, ...lanes.map(peak));
  const errors = loaded?.errors ?? [];
  const derived = loaded !== null && loaded.statuses === null;
  useTabReady("statistics", loaded !== null, errors);

  const offline = lanes.filter((l) => !l.online).map((l) => l.name);
  const machineNames = lanes.map((l) => l.name).join(",");
  const offlineNames = offline.join(",");
  useEffect(() => {
    // `statistics` is this tab's slice of #agent-state (AgentStatisticsState in store.ts).
    setAgentState({
      statistics: loaded
        ? {
            machines: machineNames ? machineNames.split(",") : [],
            offline: offlineNames ? offlineNames.split(",") : [],
            range,
            view,
            source: derived ? "runs" : "machines/status",
          }
        : null,
    });
    return () => setAgentState({ statistics: null });
  }, [loaded !== null, machineNames, offlineNames, range, view, derived]);

  return (
    <main id="main" className="stats" tabIndex={-1} data-live-flash={live.flash || undefined}>
      <div className="stats__head">
        <h1 className="stats__title">Statistics</h1>
        <RangeControl range={range} onChange={setRange} />
        <button
          type="button"
          className="stats__toggle"
          onClick={() => setView(view === "lanes" ? "table" : "lanes")}
        >
          {view === "lanes" ? "Show as table" : "Show as lanes"}
        </button>
      </div>

      {errors.length > 0 ? (
        <p className="notice notice--error stats__notice" role="alert">
          {errors.join(" · ")}
        </p>
      ) : null}
      {derived ? (
        <output className="stats__note">
          Live load and queue depth are not available right now. Lanes show what runs record:
          runs per {rangeSpec(range).unit}, ok and failed.
        </output>
      ) : null}

      {view === "lanes" ? (
        <Fragment>
          <div className="stats__cols" aria-hidden="true">
            <span />
            <span>Load</span>
            <span>Working on</span>
            <span>{RANGE_UNIT_LABEL(range)}</span>
            <span>Runs</span>
          </div>
          {lanes.map((lane) => (
            <LaneRow key={lane.name} lane={lane} range={range} scale={scale} />
          ))}
        </Fragment>
      ) : (
        <Tables lanes={lanes} range={range} />
      )}
      {loaded && lanes.length === 0 && errors.length === 0 ? (
        <p className="stats__note">No machines enrolled yet.</p>
      ) : null}
    </main>
  );
}

export default StatisticsBoard;
