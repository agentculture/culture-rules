import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, listMachines, listRules } from "../api/client";
import { settleAll } from "../api/settle";
import { useLiveUpdates, type LiveChange } from "../api/live";
import type { Machine, Placement, Rule, RunSummary } from "../api/types";
import {
  createWorkflowDef,
  deleteWorkflowDef,
  getRun,
  listActors,
  listWorkflowDefs,
  listWorkflowRuns,
  putWorkflowDef,
  restoreWorkflowDef,
  setWorkflowEnabled,
  startRun,
  type Actor,
  type RunDoc,
  type WorkflowDef,
} from "../api/workflows";
import { setWorkflowsState } from "../workflows/agentState";
import WorkflowCanvas from "../workflows/Canvas";
import IoControls from "../workflows/IoControls";
import {
  addStep,
  connect,
  deleteStep,
  runOverlay,
  setPlacement,
  toDefinition,
  toggleStep,
  type Connection,
} from "../workflows/model";
import PlacementEditor from "../workflows/PlacementEditor";
import { Switch } from "../culture-design/stages";
import StepEditor from "../workflows/StepEditor";
import WorkflowNameForm from "../workflows/WorkflowNameForm";
import { ago, slugFor } from "./rules-view";
import { useTabReady } from "./useTabReady";
import "../workflows/workflows.css";

interface Loaded {
  workflows: WorkflowDef[];
  machines: Machine[];
  actors: Actor[];
  rules: Rule[];
  /** GET /workflows answered: an empty list really is "no workflows yet". */
  listed: boolean;
  errors: string[];
}

const message = (err: unknown) => (err instanceof ApiError ? err.message : String(err));
const settledError = (r: PromiseSettledResult<unknown>) =>
  r.status === "rejected" ? message(r.reason) : null;
const value = <T,>(r: PromiseSettledResult<T>, fallback: T): T =>
  r.status === "fulfilled" ? r.value : fallback;

const RUN_DONE = new Set(["succeeded", "failed", "cancelled"]);
const POLL_MS = 2000;
/** Ids tried past a taken one (a 409: a live or soft-deleted workflow holds it). */
const MAX_ID_TRIES = 20;
const LIVE_COLLECTIONS = ["workflows", "runs"] as const;

type Editing = { kind: "placement" | "step"; id: string; trigger: HTMLElement | null } | null;
type Draft = { id: string; def: WorkflowDef; dirty: boolean };
type OverlaidRun = { id: string; doc: RunDoc | null; error: string | null };

/** The draft with `fn` applied, marked dirty (no draft stays none). */
const editDraft = (d: Draft | null, fn: (wf: WorkflowDef) => WorkflowDef): Draft | null =>
  d ? { ...d, def: fn(d.def), dirty: true } : d;

/** The loaded lists with `doc` replacing its stored copy. */
const replaceIn = (l: Loaded | null, doc: WorkflowDef): Loaded | null =>
  l ? { ...l, workflows: l.workflows.map((w) => (w.id === doc.id ? doc : w)) } : l;

/** The loaded lists with `doc` (re)added at the end. */
const putIn = (l: Loaded | null, doc: WorkflowDef): Loaded | null =>
  l ? { ...l, workflows: [...l.workflows.filter((w) => w.id !== doc.id), doc] } : l;

/** The loaded lists without workflow `id`. */
const dropFrom = (l: Loaded | null, id: string): Loaded | null =>
  l ? { ...l, workflows: l.workflows.filter((w) => w.id !== id) } : l;

/** An answer that really is a stored definition, else what was sent. */
const storedOr = (doc: WorkflowDef | null | undefined, fallback: WorkflowDef): WorkflowDef =>
  typeof doc?.id === "string" ? doc : fallback;

const emptyWorkflow = (id: string, name: string): WorkflowDef => ({
  id,
  name,
  inputs: [],
  variables: [],
  steps: [],
  edges: [],
  outputs: [],
  enabled: true,
});

/**
 * `POST /workflows` under the name's slug; a 409 (the id is held, e.g. by a
 * soft-deleted workflow) tries the next free one, up to MAX_ID_TRIES.
 */
async function createWithFreeId(name: string, taken: string[], attempt = 0): Promise<WorkflowDef> {
  const def = emptyWorkflow(slugFor(name, taken, "workflow"), name);
  try {
    return storedOr(await createWorkflowDef(def), def);
  } catch (err) {
    const held = err instanceof ApiError && err.status === 409 && attempt < MAX_ID_TRIES;
    if (!held) throw err;
    return createWithFreeId(name, [...taken, def.id], attempt + 1);
  }
}

/** Workflows, machines, actors and rules, re-read whenever `reload` moves. */
function useWorkflowsLoad(reload: number) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    settleAll(
      [
        listWorkflowDefs(controller.signal),
        listMachines(controller.signal),
        listActors(controller.signal),
        listRules(controller.signal),
      ],
      (results) => {
        if (controller.signal.aborted) return;
        const [workflows, machines, actors, rules] = results;
        setLoaded({
          workflows: value(workflows, [] as WorkflowDef[]),
          machines: value(machines, [] as Machine[]),
          actors: value(actors, [] as Actor[]),
          rules: value(rules, [] as Rule[]),
          listed: workflows.status === "fulfilled",
          errors: results.map(settledError).filter((m): m is string => m !== null),
        });
      },
      (message) => {
        // Applying the load failed: show an empty board with the failure named.
        if (controller.signal.aborted) return;
        setLoaded({
          workflows: [],
          machines: [],
          actors: [],
          rules: [],
          listed: false,
          errors: [message],
        });
      },
    );
    return () => controller.abort();
  }, [reload]);
  return [loaded, setLoaded] as const;
}

/** Recent runs of this workflow (contextual, never a tab). */
function useRecentRuns(workflowId: string | undefined, runStatus: string | undefined, tick: number) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  useEffect(() => {
    if (!workflowId) return;
    const controller = new AbortController();
    listWorkflowRuns(workflowId, 50, controller.signal)
      .then((items) => setRuns(items.slice(0, 5)))
      .catch(() => {
        if (!controller.signal.aborted) setRuns([]);
      });
    return () => controller.abort();
  }, [workflowId, runStatus, tick]);
  return runs;
}

/** The overlaid run, read from persisted run state and polled until it finishes. */
function useOverlaidRun(runId: string | null, tick: number) {
  const [run, setRun] = useState<OverlaidRun | null>(null);
  useEffect(() => {
    if (!runId) {
      setRun(null);
      return;
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = () =>
      getRun(runId, controller.signal)
        .then((doc) => {
          if (controller.signal.aborted) return;
          setRun({ id: runId, doc, error: null });
          if (!RUN_DONE.has(doc.status)) timer = setTimeout(load, POLL_MS);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setRun({ id: runId, doc: null, error: message(err) });
        });
    setRun((r) => (r?.id === runId ? r : { id: runId, doc: null, error: null }));
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId, tick]);
  return [run, setRun] as const;
}

const PencilIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 20h4L19 9l-4-4L4 16v4z" />
  </svg>
);
const TrashIcon = () => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
  </svg>
);
const PlusIcon = ({ size }: Readonly<{ size: number }>) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
    <path d="M12 5v14M5 12h14" />
  </svg>
);

/** The head's title when no workflow is open: creating, none yet, or (sr-only) the tab name. */
function HeadTitle({ creating, empty }: Readonly<{ creating: boolean; empty: boolean }>) {
  if (creating) return <h1 className="wf-title">New workflow</h1>;
  if (empty) return <h1 className="wf-title">No workflows yet</h1>;
  return <h1 className="wf-title sr-only">Workflows</h1>;
}

/** The open workflow's head: name, rename, version, the workflow switcher, enable and delete. */
function WorkflowHead({
  name,
  current,
  workflows,
  renaming,
  renameButton,
  onRename,
  onSwitch,
  onToggle,
  onDelete,
}: Readonly<{
  name: string;
  current: WorkflowDef;
  workflows: WorkflowDef[];
  renaming: boolean;
  renameButton: RefObject<HTMLButtonElement>;
  onRename: () => void;
  onSwitch: (id: string) => void;
  onToggle: () => void;
  onDelete: () => void;
}>) {
  return (
    <>
      <h1 className="wf-title">{name}</h1>
      <button
        ref={renameButton}
        type="button"
        className="icon-button wf-head__icon"
        aria-label="Rename workflow"
        aria-pressed={renaming}
        onClick={onRename}
      >
        <PencilIcon />
      </button>
      <span className="wf-version">v{current.version ?? 1}</span>
      {workflows.length > 1 ? (
        <select
          className="wf-switch"
          aria-label="Workflow"
          value={current.id}
          onChange={(e) => onSwitch(e.target.value)}
        >
          {workflows.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      ) : null}
      <span className="wf-head__tools">
        <Switch label="Workflow enabled" checked={current.enabled !== false} onChange={onToggle} />
        <button
          type="button"
          className="icon-button icon-button--danger wf-head__icon"
          aria-label="Delete workflow"
          onClick={onDelete}
        >
          <TrashIcon />
        </button>
      </span>
    </>
  );
}

/** The workflow's recent runs (contextual — runs are never a tab); each toggles the overlay. */
function RecentRuns({
  runs,
  runId,
  params,
  now,
}: Readonly<{ runs: RunSummary[]; runId: string | null; params: URLSearchParams; now: number }>) {
  if (runs.length === 0) return null;
  return (
    <nav className="wf-runs" aria-label="Recent runs">
      <span className="wf-runs__title">Recent runs</span>
      {runs.map((r) => {
        const active = r.id === runId;
        const next = new URLSearchParams(params);
        if (active) next.delete("run");
        else next.set("run", r.id);
        return (
          <Link
            key={r.id}
            className={`wf-runs__run${active ? " is-active" : ""}`}
            to={`?${next.toString()}`}
            aria-current={active ? "true" : undefined}
            data-run-status={r.status}
          >
            <span className={`run-dot run-dot--${r.status}`} aria-hidden="true" />
            {r.status} · {ago(r.created_at, now)}
          </Link>
        );
      })}
    </nav>
  );
}

/** The empty state: "New workflow" as the primary action. */
function EmptyWorkflows({ onNew }: Readonly<{ onNew: () => void }>) {
  return (
    <section className="wf-empty" aria-label="No workflows yet">
      <button type="button" className="wf-button wf-button--primary wf-button--large" onClick={onNew}>
        <PlusIcon size={20} />
        New workflow
      </button>
      <p className="wf-empty__hint">Name it, then add steps with +.</p>
    </section>
  );
}

/** The floating editor for the step being edited: its placement, or its ports. */
function StepPanels({
  editing,
  workflow,
  loaded,
  edit,
  onChange,
  onClose,
}: Readonly<{
  editing: Editing;
  workflow: WorkflowDef;
  loaded: Loaded | null;
  edit: (fn: (wf: WorkflowDef) => WorkflowDef) => void;
  onChange: (wf: WorkflowDef) => void;
  onClose: () => void;
}>) {
  const step = editing ? (workflow.steps ?? []).find((s) => s.id === editing.id) : undefined;
  if (!editing || !step) return null;
  if (editing.kind === "placement") {
    return (
      <PlacementEditor
        key={step.id}
        step={step}
        machines={loaded?.machines ?? []}
        actors={loaded?.actors ?? []}
        returnFocus={editing.trigger}
        onApply={(p: Placement | null) => {
          edit((wf) => setPlacement(wf, step.id, p));
          onClose();
        }}
        onClose={onClose}
      />
    );
  }
  return (
    <StepEditor
      workflow={workflow}
      stepId={step.id}
      returnFocus={editing.trigger}
      onChange={onChange}
      onClose={onClose}
    />
  );
}

/** What the Run button says when hovered. */
const runTitle = (rule: Rule | undefined) => (rule ? `Starts ${rule.name}` : "No rule runs this workflow yet");

/** Live updates: which ticks a batch of changes moves. */
function routeLiveChanges(
  changes: LiveChange[],
  runId: string | null,
  bump: { reload: () => void; runs: () => void; run: () => void },
) {
  if (changes.some((c) => c.collection === "workflows")) bump.reload();
  const runChanges = changes.filter((c) => c.collection === "runs");
  if (runChanges.length > 0) bump.runs();
  if (runId && runChanges.some((c) => c.id === runId)) bump.run();
}

/** The Workflows tab's agent-state snapshot (null until the board has loaded). */
function agentSnapshot(
  loaded: Loaded | null,
  snap: { count: number; selected: string | null; stepKey: string; step: string | null; dirty: boolean; run: RunDoc | null },
) {
  if (!loaded) return null;
  return {
    count: snap.count,
    selected: snap.selected,
    steps: snap.stepKey ? snap.stepKey.split(",") : [],
    step: snap.step,
    dirty: snap.dirty,
    run: snap.run ? { id: snap.run.id, status: snap.run.status } : null,
  };
}

/**
 * The Workflows tab — the 'Chosen — Workflows' board (design canvas row
 * 'Chosen', direction B "Map"): the workflow's name and version, the io
 * group (Import / Export / repository) and Run on top; below it the node
 * canvas, Inputs → steps → Outputs, each step tinted by the machine it is
 * placed on, dashed wires where data crosses machines; a legend and the
 * workflow's recent runs (contextual — runs are never a tab) underneath.
 *
 * Selection is in the query string: `?id=<workflow>&run=<run id>`. Edits
 * stay a local draft until Save (`PUT /workflows/{id}`).
 *
 * "New workflow" (in the io group, and the empty state's primary action)
 * asks only for a name, creates the workflow (`POST /workflows`, no steps)
 * and opens it on the canvas with the step `+` focused. Rename edits the
 * draft's name (Save writes it, like every canvas edit). The head's switch
 * enables / disables the stored workflow and its delete is a soft delete
 * with Undo (`POST /workflows/{id}/restore`), as on the Rules tab.
 */
export function Workflows() {
  const [params, setParams] = useSearchParams();
  const [reload, setReload] = useState(0);
  const [loaded, setLoaded] = useWorkflowsLoad(reload);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [status, setStatus] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createBusy, setCreateBusy] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [focusAddStep, setFocusAddStep] = useState<string | null>(null);
  const [deleted, setDeleted] = useState<WorkflowDef | null>(null);
  const [renaming, setRenaming] = useState(false);
  const newButton = useRef<HTMLButtonElement>(null);
  const renameButton = useRef<HTMLButtonElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  // Live: `runsTick` re-reads the recent runs, `runTick` the overlaid run.
  const [runsTick, setRunsTick] = useState(0);
  const [runTick, setRunTick] = useState(0);

  const workflows = loaded?.workflows ?? [];
  const wantedId = params.get("id");
  const current = workflows.find((w) => w.id === wantedId) ?? workflows[0] ?? null;
  const runId = params.get("run");

  // A fresh draft whenever the selected workflow (or its stored copy) changes;
  // unsaved edits to the same workflow survive a reload.
  useEffect(() => {
    if (!current) {
      setDraft(null);
      return;
    }
    setDraft((d) =>
      d?.id === current.id && d.dirty ? d : { id: current.id, def: toDefinition(current), dirty: false },
    );
  }, [current]);
  useEffect(() => {
    setSelectedStep(null);
    setEditing(null);
    setRenaming(false);
  }, [current?.id]);

  const workflow = current && draft?.id === current.id ? draft.def : null;
  const empty = loaded !== null && loaded.listed && workflows.length === 0;

  // A workflow just created opens with the step `+` focused: add the first step.
  useEffect(() => {
    if (!focusAddStep || workflow?.id !== focusAddStep) return;
    stageRef.current?.querySelector<HTMLButtonElement>(".wf-add-step")?.focus();
    setFocusAddStep(null);
  }, [focusAddStep, workflow?.id]);
  const edit = useCallback((fn: (wf: WorkflowDef) => WorkflowDef) => {
    setDraft((d) => editDraft(d, fn));
  }, []);

  const [run, setRun] = useOverlaidRun(runId, runTick);
  const runs = useRecentRuns(current?.id, run?.doc?.status, runsTick);

  // Live updates: a stored workflow changed (another editor, an import) or a run
  // moved. Unsaved edits survive a workflows refetch (the draft effect above).
  const onLive = useCallback(
    (changes: LiveChange[]) =>
      routeLiveChanges(changes, runId, {
        reload: () => setReload((n) => n + 1),
        runs: () => setRunsTick((n) => n + 1),
        run: () => setRunTick((n) => n + 1),
      }),
    [runId],
  );
  const live = useLiveUpdates(LIVE_COLLECTIONS, onLive);

  const overlay = useMemo(() => (run?.doc ? runOverlay(run.doc) : null), [run?.doc]);
  const runSettled = !runId || (run?.id === runId && (run.doc !== null || run.error !== null));
  const errors = [
    ...(loaded?.errors ?? []),
    ...(run?.error ? [`run ${run.id}: ${run.error}`] : []),
    ...(actionError ? [actionError] : []),
  ];
  const ready = loaded !== null && runSettled;
  useTabReady("workflows", ready, errors);

  const stepIds = (workflow?.steps ?? []).map((s) => s.id);
  const stepKey = stepIds.join(",");
  useEffect(() => {
    setWorkflowsState(
      agentSnapshot(loaded, {
        count: workflows.length,
        selected: current?.id ?? null,
        stepKey,
        step: selectedStep,
        dirty: draft?.dirty ?? false,
        run: run?.doc ?? null,
      }),
    );
  }, [loaded, workflows.length, current?.id, stepKey, selectedStep, draft?.dirty, run?.doc]); // eslint-disable-line react-hooks/exhaustive-deps

  const setQuery = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v === null) next.delete(k);
      else next.set(k, v);
    }
    setParams(next);
  };

  const onToggle = useCallback((id: string) => edit((wf) => toggleStep(wf, id)), [edit]);
  const onDelete = useCallback(
    (id: string) => {
      edit((wf) => deleteStep(wf, id));
      setSelectedStep(null);
      setEditing(null);
    },
    [edit],
  );
  const onPlacement = useCallback(
    (id: string, trigger: HTMLElement) => setEditing({ kind: "placement", id, trigger }),
    [],
  );
  const onEdit = useCallback(
    (id: string, trigger: HTMLElement) => setEditing({ kind: "step", id, trigger }),
    [],
  );
  const onConnect = useCallback(
    (c: Connection) =>
      edit((wf) => {
        const res = connect(wf, c);
        return res.ok ? res.workflow : wf;
      }),
    [edit],
  );
  const onRefused = useCallback((reason: string) => setStatus(`Not wired: ${reason}`), []);
  const onSelect = useCallback((id: string | null) => setSelectedStep(id), []);
  const onAddStep = useCallback(() => {
    if (!workflow) return;
    const res = addStep(workflow);
    setDraft((d) => editDraft(d, () => res.workflow));
    setSelectedStep(res.id);
  }, [workflow]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setActionError(null);
    try {
      const saved = await putWorkflowDef(toDefinition(draft.def));
      setDraft({ id: saved.id, def: toDefinition(saved), dirty: false });
      setLoaded((l) => replaceIn(l, saved));
      setStatus(`Saved ${saved.name}`);
    } catch (err) {
      setActionError(`Save failed: ${message(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const runRule = current
    ? (loaded?.rules ?? []).find((r) => r.workflow?.id === current.id)
    : undefined;
  const start = async () => {
    if (!runRule) return;
    setActionError(null);
    try {
      const doc = await startRun(runRule.id);
      setRun({ id: doc.id, doc, error: null });
      setQuery({ run: doc.id });
    } catch (err) {
      setActionError(`Run failed: ${message(err)}`);
    }
  };

  const openNew = () => {
    setCreateError(null);
    setRenaming(false);
    setCreating(true);
  };
  const closeRename = () => {
    setRenaming(false);
    requestAnimationFrame(() => renameButton.current?.focus());
  };
  const closeNew = () => {
    setCreating(false);
    setCreateError(null);
    // The opener: the header button (the empty state's comes back as a new element).
    requestAnimationFrame(() => newButton.current?.focus());
  };
  const create = async (name: string) => {
    setCreateBusy(true);
    setCreateError(null);
    try {
      const stored = await createWithFreeId(
        name,
        workflows.map((w) => w.id),
      );
      setLoaded((l) => putIn(l, stored));
      setCreating(false);
      setDeleted(null);
      setFocusAddStep(stored.id);
      setActionError(null);
      setStatus(`Created ${stored.name}`);
      setQuery({ id: stored.id, run: null });
    } catch (err) {
      setCreateError(message(err));
    } finally {
      setCreateBusy(false);
    }
  };

  const toggleWorkflow = async () => {
    if (!current) return;
    const enabled = current.enabled === false;
    setActionError(null);
    try {
      const doc = await setWorkflowEnabled(current.id, enabled);
      setLoaded((l) => replaceIn(l, storedOr(doc, { ...current, enabled })));
      // Unsaved edits survive; they carry the new state so Save does not undo it.
      setDraft((d) => (d?.id === current.id && d.dirty ? { ...d, def: { ...d.def, enabled } } : d));
      setStatus(`${enabled ? "Enabled" : "Disabled"} ${current.name}`);
    } catch (err) {
      setActionError(`${enabled ? "Enable" : "Disable"} failed: ${message(err)}`);
    }
  };
  const removeWorkflow = async () => {
    if (!current) return;
    const gone = current;
    const index = workflows.findIndex((w) => w.id === gone.id);
    const next = workflows[index + 1] ?? workflows[index - 1];
    setActionError(null);
    try {
      await deleteWorkflowDef(gone.id);
      setDeleted(gone);
      setStatus("");
      setLoaded((l) => dropFrom(l, gone.id));
      setQuery({ id: next?.id ?? null, run: null });
    } catch (err) {
      setActionError(`Delete failed: ${message(err)}`);
    }
  };
  const undoDelete = async () => {
    if (!deleted) return;
    setActionError(null);
    try {
      const back = storedOr(await restoreWorkflowDef(deleted.id), deleted);
      setLoaded((l) => putIn(l, back));
      setDeleted(null);
      setStatus(`Restored ${back.name}`);
      setQuery({ id: back.id, run: null });
    } catch (err) {
      setActionError(`Undo failed: ${message(err)}`);
    }
  };

  const now = Date.now();

  let body: ReactNode = null;
  if (creating) {
    body = (
      <WorkflowNameForm
        label="New workflow"
        submitLabel="Create workflow"
        busy={createBusy}
        error={createError}
        onSubmit={(name) => void create(name)}
        onCancel={closeNew}
      />
    );
  } else if (workflow) {
    body = (
      <div className="wf-stage" ref={stageRef}>
        {renaming ? (
          <WorkflowNameForm
            key={workflow.id}
            label="Rename workflow"
            submitLabel="Rename"
            initial={workflow.name}
            onSubmit={(name) => {
              if (name !== workflow.name) edit((wf) => ({ ...wf, name }));
              closeRename();
            }}
            onCancel={closeRename}
          />
        ) : null}
        <WorkflowCanvas
          workflow={workflow}
          machines={loaded?.machines ?? []}
          actors={loaded?.actors ?? []}
          overlay={overlay}
          selected={selectedStep}
          onSelect={onSelect}
          onToggle={onToggle}
          onPlacement={onPlacement}
          onEdit={onEdit}
          onDelete={onDelete}
          onAddStep={onAddStep}
          onConnect={onConnect}
          onRefused={onRefused}
        />
        <StepPanels
          editing={editing}
          workflow={workflow}
          loaded={loaded}
          edit={edit}
          onChange={(wf) => setDraft((d) => editDraft(d, () => wf))}
          onClose={() => setEditing(null)}
        />
      </div>
    );
  } else if (empty) {
    body = <EmptyWorkflows onNew={openNew} />;
  }

  return (
    <main id="main" className="wf-board" tabIndex={-1} data-live-flash={live.flash || undefined}>
      {errors.length > 0 ? (
        <p className="notice notice--error wf-notice" role="alert">
          {errors.join(" · ")}
        </p>
      ) : null}
      {deleted ? (
        <output className="notice notice--undo wf-notice">
          <span>Deleted {deleted.name}</span>
          <button type="button" className="wf-button" onClick={() => void undoDelete()}>
            Undo
          </button>
          <button
            type="button"
            className="icon-button icon-button--small"
            aria-label="Dismiss"
            onClick={() => setDeleted(null)}
          >
            ×
          </button>
        </output>
      ) : null}
      <div className="wf-head">
        {!creating && current && workflow ? (
          <WorkflowHead
            name={workflow.name}
            current={current}
            workflows={workflows}
            renaming={renaming}
            renameButton={renameButton}
            onRename={() => (renaming ? closeRename() : setRenaming(true))}
            onSwitch={(id) => setQuery({ id, run: null })}
            onToggle={() => void toggleWorkflow()}
            onDelete={() => void removeWorkflow()}
          />
        ) : (
          <HeadTitle creating={creating} empty={empty} />
        )}
        <span className="wf-head__end">
          {loaded ? (
            <button
              ref={newButton}
              type="button"
              className="wf-button"
              aria-label="New workflow"
              aria-expanded={creating}
              onClick={() => (creating ? closeNew() : openNew())}
            >
              <PlusIcon size={16} />
              New
            </button>
          ) : null}
          <IoControls
            onImported={() => setReload((n) => n + 1)}
            onStatus={(t) => {
              setActionError(null);
              setStatus(t);
            }}
            onError={setActionError}
          />
        </span>
        {draft?.dirty && !creating ? (
          <button
            type="button"
            className="wf-button wf-button--save"
            disabled={saving}
            onClick={() => void save()}
          >
            Save
          </button>
        ) : null}
        {creating ? null : (
          <button
            type="button"
            className="wf-run"
            disabled={!runRule}
            title={runTitle(runRule)}
            onClick={() => void start()}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M7 4v16l13-8z" />
            </svg>
            Run
          </button>
        )}
      </div>
      <output className="wf-status">{status}</output>

      {body}

      <div className="wf-foot">
        <span className="wf-legend">
          <svg width="34" height="4" aria-hidden="true">
            <path d="M0 2 H34" className="wf-legend__line" />
          </svg>
          same machine
        </span>
        <span className="wf-legend">
          <svg width="34" height="4" aria-hidden="true">
            <path d="M0 2 H34" className="wf-legend__line wf-legend__line--cross" />
          </svg>
          crosses machines
        </span>
        <RecentRuns runs={runs} runId={runId} params={params} now={now} />
      </div>
    </main>
  );
}

export default Workflows;
