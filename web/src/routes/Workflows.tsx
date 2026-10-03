import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [reload, setReload] = useState(0);
  const [draft, setDraft] = useState<{ id: string; def: WorkflowDef; dirty: boolean } | null>(null);
  const [selectedStep, setSelectedStep] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);
  const [status, setStatus] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [run, setRun] = useState<{ id: string; doc: RunDoc | null; error: string | null } | null>(
    null,
  );
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
      d && d.id === current.id && d.dirty
        ? d
        : { id: current.id, def: toDefinition(current), dirty: false },
    );
  }, [current]);
  useEffect(() => {
    setSelectedStep(null);
    setEditing(null);
    setRenaming(false);
  }, [current?.id]);

  const workflow = draft && current && draft.id === current.id ? draft.def : null;
  const empty = loaded !== null && loaded.listed && workflows.length === 0;

  // A workflow just created opens with the step `+` focused: add the first step.
  useEffect(() => {
    if (!focusAddStep || workflow?.id !== focusAddStep) return;
    stageRef.current?.querySelector<HTMLButtonElement>(".wf-add-step")?.focus();
    setFocusAddStep(null);
  }, [focusAddStep, workflow?.id]);
  const edit = useCallback((fn: (wf: WorkflowDef) => WorkflowDef) => {
    setDraft((d) => (d ? { ...d, def: fn(d.def), dirty: true } : d));
  }, []);

  // Recent runs of this workflow (contextual, never a tab).
  const runStatus = run?.doc?.status;
  useEffect(() => {
    if (!current) return;
    const controller = new AbortController();
    listWorkflowRuns(current.id, 50, controller.signal)
      .then((items) => setRuns(items.slice(0, 5)))
      .catch(() => {
        if (!controller.signal.aborted) setRuns([]);
      });
    return () => controller.abort();
  }, [current?.id, runStatus, runsTick]); // eslint-disable-line react-hooks/exhaustive-deps

  // The overlaid run, read from persisted run state and polled until it finishes.
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
    setRun((r) => (r && r.id === runId ? r : { id: runId, doc: null, error: null }));
    void load();
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [runId, runTick]);

  // Live updates: a stored workflow changed (another editor, an import) or a run
  // moved. Unsaved edits survive a workflows refetch (the draft effect above).
  const onLive = useCallback(
    (changes: LiveChange[]) => {
      if (changes.some((c) => c.collection === "workflows")) setReload((n) => n + 1);
      const runChanges = changes.filter((c) => c.collection === "runs");
      if (runChanges.length > 0) setRunsTick((n) => n + 1);
      if (runId && runChanges.some((c) => c.id === runId)) setRunTick((n) => n + 1);
    },
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
      loaded
        ? {
            count: workflows.length,
            selected: current?.id ?? null,
            steps: stepKey ? stepKey.split(",") : [],
            step: selectedStep,
            dirty: draft?.dirty ?? false,
            run: run?.doc ? { id: run.doc.id, status: run.doc.status } : null,
          }
        : null,
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
    setDraft((d) => (d ? { ...d, def: res.workflow, dirty: true } : d));
    setSelectedStep(res.id);
  }, [workflow]);

  const save = async () => {
    if (!draft) return;
    setSaving(true);
    setActionError(null);
    try {
      const saved = await putWorkflowDef(toDefinition(draft.def));
      setDraft({ id: saved.id, def: toDefinition(saved), dirty: false });
      setLoaded((l) =>
        l ? { ...l, workflows: l.workflows.map((w) => (w.id === saved.id ? saved : w)) } : l,
      );
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
    const taken = workflows.map((w) => w.id);
    try {
      for (let attempt = 0; ; attempt++) {
        const def: WorkflowDef = {
          id: slugFor(name, taken, "workflow"),
          name,
          inputs: [],
          variables: [],
          steps: [],
          edges: [],
          outputs: [],
          enabled: true,
        };
        let made: WorkflowDef | null;
        try {
          made = await createWorkflowDef(def);
        } catch (err) {
          // 409: the id is held (a soft-deleted workflow keeps it) — try the next one.
          if (err instanceof ApiError && err.status === 409 && attempt < MAX_ID_TRIES) {
            taken.push(def.id);
            continue;
          }
          throw err;
        }
        const stored = made && typeof made.id === "string" ? made : def;
        setLoaded((l) =>
          l ? { ...l, workflows: [...l.workflows.filter((w) => w.id !== stored.id), stored] } : l,
        );
        setCreating(false);
        setDeleted(null);
        setFocusAddStep(stored.id);
        setActionError(null);
        setStatus(`Created ${stored.name}`);
        setQuery({ id: stored.id, run: null });
        return;
      }
    } catch (err) {
      setCreateError(message(err));
    } finally {
      setCreateBusy(false);
    }
  };

  const replaceStored = (doc: WorkflowDef) =>
    setLoaded((l) =>
      l ? { ...l, workflows: l.workflows.map((w) => (w.id === doc.id ? doc : w)) } : l,
    );
  const toggleWorkflow = async () => {
    if (!current) return;
    const enabled = current.enabled === false;
    setActionError(null);
    try {
      const doc = await setWorkflowEnabled(current.id, enabled);
      replaceStored(doc && typeof doc.id === "string" ? doc : { ...current, enabled });
      // Unsaved edits survive; they carry the new state so Save does not undo it.
      setDraft((d) =>
        d && d.id === current.id && d.dirty ? { ...d, def: { ...d.def, enabled } } : d,
      );
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
      setLoaded((l) => (l ? { ...l, workflows: l.workflows.filter((w) => w.id !== gone.id) } : l));
      setQuery({ id: next ? next.id : null, run: null });
    } catch (err) {
      setActionError(`Delete failed: ${message(err)}`);
    }
  };
  const undoDelete = async () => {
    if (!deleted) return;
    setActionError(null);
    try {
      const doc = await restoreWorkflowDef(deleted.id);
      const back = doc && typeof doc.id === "string" ? doc : deleted;
      setLoaded((l) =>
        l ? { ...l, workflows: [...l.workflows.filter((w) => w.id !== back.id), back] } : l,
      );
      setDeleted(null);
      setStatus(`Restored ${back.name}`);
      setQuery({ id: back.id, run: null });
    } catch (err) {
      setActionError(`Undo failed: ${message(err)}`);
    }
  };

  const editingStep = editing
    ? (workflow?.steps ?? []).find((s) => s.id === editing.id)
    : undefined;
  const now = Date.now();

  return (
    <main id="main" className="wf-board" tabIndex={-1} data-live-flash={live.flash || undefined}>
      {errors.length > 0 ? (
        <p className="notice notice--error wf-notice" role="alert">
          {errors.join(" · ")}
        </p>
      ) : null}
      {deleted ? (
        <p className="notice notice--undo wf-notice" role="status">
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
        </p>
      ) : null}
      <div className="wf-head">
        {creating ? (
          <h1 className="wf-title">New workflow</h1>
        ) : current && workflow ? (
          <>
            <h1 className="wf-title">{workflow.name}</h1>
            <button
              ref={renameButton}
              type="button"
              className="icon-button wf-head__icon"
              aria-label="Rename workflow"
              aria-pressed={renaming}
              onClick={() => (renaming ? closeRename() : setRenaming(true))}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <path d="M4 20h4L19 9l-4-4L4 16v4z" />
              </svg>
            </button>
            <span className="wf-version">v{current.version ?? 1}</span>
            {workflows.length > 1 ? (
              <select
                className="wf-switch"
                aria-label="Workflow"
                value={current.id}
                onChange={(e) => setQuery({ id: e.target.value, run: null })}
              >
                {workflows.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.name}
                  </option>
                ))}
              </select>
            ) : null}
            <span className="wf-head__tools">
              <Switch
                label="Workflow enabled"
                checked={current.enabled !== false}
                onChange={() => void toggleWorkflow()}
              />
              <button
                type="button"
                className="icon-button icon-button--danger wf-head__icon"
                aria-label="Delete workflow"
                onClick={() => void removeWorkflow()}
              >
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" />
                </svg>
              </button>
            </span>
          </>
        ) : empty ? (
          <h1 className="wf-title">No workflows yet</h1>
        ) : (
          <h1 className="wf-title sr-only">Workflows</h1>
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
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
                <path d="M12 5v14M5 12h14" />
              </svg>
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
            title={runRule ? `Starts ${runRule.name}` : "No rule runs this workflow yet"}
            onClick={() => void start()}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
              <path d="M7 4v16l13-8z" />
            </svg>
            Run
          </button>
        )}
      </div>
      <p className="wf-status" role="status">
        {status}
      </p>

      {creating ? (
        <WorkflowNameForm
          label="New workflow"
          submitLabel="Create workflow"
          busy={createBusy}
          error={createError}
          onSubmit={(name) => void create(name)}
          onCancel={closeNew}
        />
      ) : workflow ? (
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
          {editing?.kind === "placement" && editingStep ? (
            <PlacementEditor
              key={editingStep.id}
              step={editingStep}
              machines={loaded?.machines ?? []}
              actors={loaded?.actors ?? []}
              returnFocus={editing.trigger}
              onApply={(p: Placement | null) => {
                edit((wf) => setPlacement(wf, editingStep.id, p));
                setEditing(null);
              }}
              onClose={() => setEditing(null)}
            />
          ) : null}
          {editing?.kind === "step" && editingStep ? (
            <StepEditor
              workflow={workflow}
              stepId={editingStep.id}
              returnFocus={editing.trigger}
              onChange={(wf) => setDraft((d) => (d ? { ...d, def: wf, dirty: true } : d))}
              onClose={() => setEditing(null)}
            />
          ) : null}
        </div>
      ) : empty ? (
        <section className="wf-empty" aria-label="No workflows yet">
          <button type="button" className="wf-button wf-button--primary wf-button--large" onClick={openNew}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
            New workflow
          </button>
          <p className="wf-empty__hint">Name it, then add steps with +.</p>
        </section>
      ) : null}

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
        {runs.length > 0 ? (
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
        ) : null}
      </div>
    </main>
  );
}

export default Workflows;
