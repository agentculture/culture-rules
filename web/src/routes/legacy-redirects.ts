/**
 * Where old links land now that rules are folded into workflows (spec c16,
 * h22; plan t8). The Rules tab is gone; `/rules` and `/rules/:ruleId` stay
 * valid addresses and redirect here:
 *
 *   /rules                -> /workflows
 *   /rules/<id>           -> /workflows/<its workflow>?entry=<id>
 *   /rules/<id>, no wf    -> /workflows?entry=<id>   (the D7 place: the list
 *                            shows it under "Rules without a workflow")
 *   /rules/<unknown>      -> /workflows?notice=rule-not-found&rule=<id>
 *
 * `/workflows/<id>` is the same page as `/workflows?id=<id>` (workflowPath).
 */
import type { Rule } from "../api/types";

export const NOTICE_RULE_NOT_FOUND = "rule-not-found";
/** The rules could not be read: the id may be fine, so never say "deleted". */
export const NOTICE_RULES_UNAVAILABLE = "rules-unavailable";

export function rulesUnavailableRedirect(ruleId: string): Redirect {
  return { to: `/workflows?notice=${NOTICE_RULES_UNAVAILABLE}&rule=${encodeURIComponent(ruleId)}` };
}

export interface Redirect {
  to: string;
}

const enc = encodeURIComponent;

export function ruleRedirect(ruleId: string | undefined, rules: readonly Rule[]): Redirect {
  if (!ruleId) return { to: "/workflows" };
  const rule = rules.find((r) => r.id === ruleId);
  if (!rule) return { to: `/workflows?notice=${NOTICE_RULE_NOT_FOUND}&rule=${enc(ruleId)}` };
  const workflowId = rule.workflow?.id;
  if (!workflowId) return { to: `/workflows?entry=${enc(ruleId)}` };
  return { to: `/workflows/${enc(workflowId)}?entry=${enc(ruleId)}` };
}

/** `/workflows/:workflowId?…` normalised to the tab's query form, `?id=` first. */
export function workflowPath(workflowId: string, search: string): string {
  const params = new URLSearchParams(search);
  params.delete("id");
  const rest = params.toString();
  const tail = rest ? "&" + rest : "";
  return `/workflows?id=${enc(workflowId)}${tail}`;
}
