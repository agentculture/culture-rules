/*
 * Guided errors (t35): every failure the editor can meet is shown as plain
 * language plus typed fix options, never as the server's raw text. The table
 * is keyed on `error.code` and on nested `errors[].code`, drawn from the
 * server vocabularies: RunError (engine/runs.py), ServiceError
 * (server/service.py), auth (auth/, server/app.py), model validation
 * (model/validate.py) and the planned codes (invalid_inputs, actor_unavailable,
 * destination_refused, extra_missing, trigger_type_required). A code that is
 * not in the table answers GENERIC_GUIDANCE.
 */
import type { ApiError } from "./client";

/** A fix a host component can offer; the host decides how to carry it out. */
export type Fix =
  | { kind: "add-input" }
  | { kind: "pick-actor" }
  | { kind: "wire-port"; port?: string }
  | { kind: "create-rule" }
  | { kind: "retry" }
  | { kind: "sign-in" }
  | { kind: "reload" }
  | { kind: "edit" };

export type FixKind = Fix["kind"];

export interface Guidance {
  /** Plain-language explanation; no code names, no server text. */
  message: string;
  /** At least one way forward, most likely first. */
  fixes: Fix[];
}

export const FIX_LABELS: Record<FixKind, string> = {
  "add-input": "Add an input",
  "pick-actor": "Pick an actor",
  "wire-port": "Wire this port",
  "create-rule": "Create a rule",
  retry: "Try again",
  "sign-in": "Sign in",
  reload: "Reload",
  edit: "Edit it",
};

export function fixLabel(fix: Fix): string {
  return FIX_LABELS[fix.kind];
}

export const GENERIC_GUIDANCE: Guidance = {
  message: "Something went wrong that the editor does not recognise. Nothing was lost; try again.",
  fixes: [{ kind: "retry" }],
};

const g = (message: string, ...fixes: Fix[]): Guidance => ({ message, fixes });
const RETRY: Fix = { kind: "retry" };
const EDIT: Fix = { kind: "edit" };
const RELOAD: Fix = { kind: "reload" };
const SIGN_IN: Fix = { kind: "sign-in" };

const TABLE: Record<string, Guidance> = {
  // transport (client.ts)
  timeout: g("The server or a step took too long to answer.", RETRY),
  aborted: g("That request was cancelled before it finished.", RETRY),
  unreachable: g("The editor cannot reach the culture-rules server.", RETRY),
  http_error: g("The server refused that request.", RETRY),

  // RunError: starting a run and containment
  paused: g("The engine is paused, so nothing fires until it is resumed.", RETRY),
  rule_not_found: g("That rule no longer exists.", { kind: "create-rule" }, RELOAD),
  workflow_not_found: g("The workflow this rule uses is missing.", EDIT, RELOAD),
  not_fireable: g("That rule or its workflow was deleted, so it cannot run.", EDIT, { kind: "create-rule" }),
  workflow_required: g("This rule needs a workflow before it can run.", EDIT),
  workflow_mismatch: g("The rule points at a different workflow than the one supplied.", EDIT),
  workflow_version_unavailable: g("The workflow version this rule is pinned to is not available.", EDIT),
  unsupported_workflow: g("This workflow uses a shape the engine cannot run yet, such as a nested loop.", EDIT),
  invalid_rule: g("This rule has problems and cannot run until they are fixed.", EDIT),
  invalid_workflow: g("This workflow has problems and cannot run until they are fixed.", EDIT),
  input_missing: g("A required workflow input has no value.", { kind: "add-input" }, { kind: "wire-port" }),
  input_type_mismatch: g("An input was given a value of the wrong type.", { kind: "wire-port" }, EDIT),
  run_not_found: g("That run no longer exists.", RELOAD),
  run_finished: g("That run has already finished.", RELOAD),
  contention: g("Another change was being recorded at the same moment.", RETRY),
  pause_refused: g("The engine is already paused.", RELOAD),
  resume_refused: g("The engine is not paused.", RELOAD),
  drain_refused: g("That machine is already drained.", RELOAD),
  undrain_refused: g("That machine is not drained.", RELOAD),
  cancelled: g("This run was cancelled.", RETRY),
  no_actor_port: g("No actor is set up to do this kind of work.", { kind: "pick-actor" }),
  no_ack: g("The actor did not confirm it received the work.", { kind: "pick-actor" }, RETRY),
  blocked: g("The actor is blocked and cannot take this work.", { kind: "pick-actor" }, RETRY),
  actor_failed: g("The actor tried and failed.", RETRY, { kind: "pick-actor" }),
  unsafe_retry: g("The outcome is unknown and the step cannot be repeated safely.", EDIT, RELOAD),
  loop_items_invalid: g("A loop was given something that is not a list.", { kind: "wire-port" }, EDIT),
  output_type_mismatch: g("An output has a different type than the workflow declares.", { kind: "wire-port" }, EDIT),

  // planned engine and API codes
  invalid_inputs: g("Some inputs are missing or have the wrong type.", { kind: "add-input" }, { kind: "wire-port" }),
  actor_unavailable: g("No actor that can do this is available right now.", { kind: "pick-actor" }, RETRY),
  destination_refused: g("The destination refused this work.", { kind: "pick-actor" }, EDIT),
  extra_missing: g("An optional feature this needs is not installed on the server.", RELOAD),
  trigger_type_required: g("A trigger needs a type before the rule can be saved.", EDIT, { kind: "create-rule" }),
  trigger_kind_unknown: g("That kind of trigger is not one the editor knows.", EDIT),
  trigger_cron_required: g("A scheduled trigger needs a schedule.", EDIT),
  trigger_param_required: g("The trigger is missing a required setting.", EDIT),
  trigger_param_invalid: g("One of the trigger's settings is not valid.", EDIT),
  action_kind_unknown: g("That kind of action is not one the editor knows.", EDIT),
  action_param_required: g("The action is missing a required setting.", EDIT, { kind: "pick-actor" }),
  action_param_type: g("One of the action's settings has the wrong type.", EDIT),
  not_implemented: g("The server does not support this yet.", RELOAD),
  replay_invalid: g("This run cannot be replayed because its rule or workflow changed.", EDIT, RELOAD),
  // variables (a rule that reads `{"var": name}`)
  variable_undefined: g(
    "This rule uses a variable that does not exist yet. Create it on the Variables tab, or pick another.",
    EDIT,
  ),
  variables_unsupported_nodes: g(
    "Part of this condition cannot read a variable, so the rule was not saved. Type a value there instead.",
    EDIT,
  ),
  not_json: g("The server answered in a form the editor cannot read.", RELOAD, RETRY),

  // ServiceError
  invalid: g("Some of what was entered is not valid.", EDIT),
  not_found: g("That item no longer exists.", RELOAD, { kind: "create-rule" }),
  conflict: g("Someone else changed this at the same time.", RELOAD),
  rule_referenced: g("Other rules still depend on this one, so it cannot be removed.", EDIT, RELOAD),
  repo_not_found: g("That repository is not set up on the server.", EDIT),
  repo_not_local: g("That repository is not on this machine.", EDIT),
  git_error: g("The repository could not be read or written.", RETRY),
  unsafe_id: g("That name has characters that are not allowed.", EDIT),
  id_mismatch: g("The name in the body does not match the one in the address.", EDIT),
  format: g("That file format is not supported.", EDIT),
  one_source: g("Choose either files or a repository, not both.", EDIT),
  unknown: g("That option is not recognised.", EDIT),
  cursor: g("The list position expired.", RELOAD),
  git: g("The repository could not be read or written.", RETRY),

  // auth
  forbidden: g("You do not have permission to do that.", SIGN_IN),
  forbidden_role: g("Your role is not allowed to make this change.", SIGN_IN),
  unauthorized: g("You need to sign in first.", SIGN_IN),
  bad_identity: g("The identity sent with this request is not valid.", SIGN_IN),
  bad_token: g("The access token is unknown or has been revoked.", SIGN_IN),
  secret_literal: g("A secret was typed in directly. Reference a stored secret instead.", EDIT),
  malformed: g("The request was not in a shape the server understands.", EDIT, RETRY),
  unknown_kid: g("Your sign-in is out of date.", SIGN_IN),
  bad_signature: g("Your sign-in could not be verified.", SIGN_IN),
  bad_issuer: g("Your sign-in came from an unexpected place.", SIGN_IN),
  bad_audience: g("Your sign-in is for a different application.", SIGN_IN),
  expired: g("Your sign-in has expired.", SIGN_IN),
  not_yet_valid: g("Your sign-in is not valid yet; check this machine's clock.", SIGN_IN, RETRY),

  // model validation (errors[].code)
  invalid_kind: g("That kind is not one the editor knows.", EDIT),
  invalid_value: g("A value is outside what is allowed.", EDIT),
  condition_invalid: g("The condition cannot be evaluated as written.", EDIT),
  cycle: g("These steps or rules loop back on themselves.", EDIT),
  duplicate: g("Two things share the same name.", EDIT),
  empty: g("Something required is empty.", EDIT, { kind: "add-input" }),
  invalid_reference: g("A reference points at something that does not exist.", { kind: "wire-port" }, EDIT),
  loop_max_required: g("A loop needs a maximum number of rounds.", EDIT),
  not_allowed: g("That is not allowed here.", EDIT),
  placement_form: g("Where this runs is not set in a valid way.", { kind: "pick-actor" }, EDIT),
  port_type_mismatch: g("These two ports carry different types of data.", { kind: "wire-port" }, EDIT),
  range: g("A number is outside its allowed range.", EDIT),
  required: g("A required field is missing.", { kind: "add-input" }, EDIT),
  reserved: g("That name is reserved.", EDIT),
  self_reference: g("Something refers to itself.", EDIT),
  trigger_reference: g("A reference to the trigger is not valid here.", { kind: "wire-port" }, EDIT),
  unknown_port: g("A connection uses a port that does not exist.", { kind: "wire-port" }, EDIT),
  unknown_step: g("A connection points at a step that does not exist.", { kind: "wire-port" }, EDIT),
};

export const KNOWN_CODES: readonly string[] = Object.freeze(Object.keys(TABLE));

const has = (code: string): boolean => Object.hasOwn(TABLE, code);

/**
 * Guidance for an error code. When the top-level code is the broad
 * `invalid`/`unknown`, a more specific nested code (errors[].code) wins.
 * Anything not in the table answers GENERIC_GUIDANCE.
 */
export function guidanceFor(code: string, nested: readonly string[] = []): Guidance {
  const broad = !has(code) || code === "invalid" || code === "invalid_rule" || code === "invalid_workflow";
  if (broad) {
    const hit = nested.find(has);
    if (hit) return TABLE[hit];
  }
  return has(code) ? TABLE[code] : GENERIC_GUIDANCE;
}

export function guidanceForError(error: ApiError | { code: string }, nested: readonly string[] = []): Guidance {
  return guidanceFor(error.code, nested);
}
