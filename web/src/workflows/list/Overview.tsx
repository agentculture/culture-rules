import type { RefObject } from "react";
import { Link } from "react-router-dom";
import type { AppSurface } from "../../api/actors";
import type { Step, WorkflowDef } from "../../api/workflows";
import { APP_SURFACES } from "../../actors/app-config";
import type { FoldModel } from "../../fold/model";
import { count, workflowUrl } from "./presentation";
import "./overview.css";

/** What is read of an actor: the id, its kind and, for an app, its surface. */
export interface ActorRef {
  id: string;
  kind?: string;
  params?: Record<string, unknown>;
}

const SURFACE_LABEL: Record<AppSurface, string> = {
  github: "GitHub",
  jira: "Jira",
  discord: "Discord",
  email: "Email",
  search: "Search",
};

/** Top-level steps plus the steps inside every loop body, however deep. */
export function stepCount(steps: readonly Step[] | undefined): number {
  return (steps ?? []).reduce((n, step) => n + 1 + stepCount(step.body), 0);
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const actorIn = (action: unknown): string | null => {
  if (!isRecord(action) || !isRecord(action.params)) return null;
  const actor = action.params.actor;
  return typeof actor === "string" ? actor : null;
};

/** Actor ids a workflow's steps use: action steps' params.actor, ai and actor_task placements. */
function stepActors(steps: readonly Step[] | undefined, out: Set<string>): void {
  for (const step of steps ?? []) {
    const config = step.config ?? {};
    const fromAction = config.builtin === "action" ? actorIn(config.action) : null;
    if (fromAction) out.add(fromAction);
    if ((step.kind === "ai" || step.kind === "actor_task") && step.placement?.actor) out.add(step.placement.actor);
    stepActors(step.body, out);
  }
}

/**
 * The app surfaces a workflow touches (c8, c24): app-kind actors with a known surface, reached
 * from its action steps, its ai / actor_task steps and the Then (and on-failure) actions of the
 * rules that start it. An id that matches no actor, or a non-app actor, is left out.
 */
export function appsTouched(wf: WorkflowDef, model: FoldModel, actors: readonly ActorRef[]): string[] {
  const ids = new Set<string>();
  stepActors(wf.steps, ids);
  for (const entry of model.workflows.find((w) => w.id === wf.id)?.entries ?? []) {
    for (const action of [entry.rule.action, entry.rule.on_failure]) {
      const id = actorIn(action);
      if (id) ids.add(id);
    }
  }
  const labels = new Set<string>();
  for (const id of ids) {
    const actor = actors.find((a) => a.id === id);
    const surface = actor?.kind === "app" ? actor.params?.surface : undefined;
    const known = APP_SURFACES.find((s) => s === surface);
    if (known) labels.add(SURFACE_LABEL[known]);
  }
  return [...labels].sort();
}

export interface OverviewProps {
  workflows: readonly WorkflowDef[];
  model: FoldModel;
  actors: readonly ActorRef[];
  /** The workflows could not be listed: what went wrong. */
  error?: string | null;
  onNew: () => void;
  onNewRule?: () => void;
  newRef?: RefObject<HTMLButtonElement>;
  newRuleRef?: RefObject<HTMLButtonElement>;
}

function Card({ wf, model, actors }: Readonly<{ wf: WorkflowDef; model: FoldModel; actors: readonly ActorRef[] }>) {
  const apps = appsTouched(wf, model, actors);
  const description = wf.description?.trim();
  return (
    <li>
      <Link className="wf-tile" to={workflowUrl(wf.id)}>
        <h2 className="wf-tile__name">{wf.name || wf.id}</h2>
        {description ? (
          <p className="wf-tile__desc">{description}</p>
        ) : (
          <p className="wf-tile__desc wf-tile__desc--none">No description yet</p>
        )}
        <p className="wf-tile__facts">
          <span>{count(stepCount(wf.steps), "step")}</span>
          {apps.map((app) => (
            <span key={app} className="wf-tile__chip">
              {app}
            </span>
          ))}
          {wf.enabled === false ? <span className="wf-tile__chip wf-tile__chip--off">Disabled</span> : null}
        </p>
      </Link>
    </li>
  );
}

/** The Workflows tab with nothing open: one card per workflow, no list pane. */
export function Overview({ workflows, model, actors, error, onNew, onNewRule, newRef, newRuleRef }: Readonly<OverviewProps>) {
  return (
    <section className="wf-overview" aria-label="Workflows overview">
      <header className="wf-overview__head">
        <div>
          <h1>{workflows.length === 0 && !error ? "No workflows yet" : "Workflows"}</h1>
          {error || workflows.length === 0 ? null : <p>{count(workflows.length, "workflow")}. Open one to see how it runs.</p>}
        </div>
        {workflows.length === 0 && !error ? null : (
        <span className="wf-overview__actions">
          <button type="button" ref={newRef} className="wf-button wf-button--primary" onClick={onNew}>
            New workflow
          </button>
          {onNewRule ? (
            <button type="button" ref={newRuleRef} className="wf-button" onClick={onNewRule}>
              New rule
            </button>
          ) : null}
        </span>
        )}
      </header>
      {error ? (
        <p className="wf-overview__error" role="alert">
          Could not list the workflows: {error}
        </p>
      ) : workflows.length === 0 ? (
        <section className="wf-empty" aria-label="No workflows yet">
          <button type="button" ref={newRef} className="wf-button wf-button--primary wf-button--large" onClick={onNew}>
            New workflow
          </button>
          <p className="wf-empty__hint">Name it, then add steps with +.</p>
        </section>
      ) : (
        <ul className="wf-tiles" aria-label="All workflows">
          {workflows.map((wf) => (
            <Card key={wf.id} wf={wf} model={model} actors={actors} />
          ))}
        </ul>
      )}
    </section>
  );
}
