import { useEffect, useRef, useState } from "react";
import { listActors, type Actor } from "../../api/actors";
import { ApiError, listRules, listWorkflows } from "../../api/client";
import { createRule } from "../../api/rules";
import { failureMessage } from "../../api/settle";
import type { Rule } from "../../api/types";
import { NewRuleForm } from "../../rules/Forms";
import "../../rules/rules.css";
import { D7Offer } from "./D7Offer";
import "./simple.css";

interface Known {
  actors: Actor[];
  ruleIds: string[];
  workflowIds: string[];
}

const errorText = (err: unknown) => (err instanceof ApiError ? err.message : failureMessage(err));

export interface NewRuleProps {
  /** Where the link to the new workflow goes. */
  hrefFor?: (workflowId: string) => string;
  /** The rule now starts this new workflow: navigate there. */
  onCreated?: (workflowId: string) => void;
  onCancel: () => void;
}

/**
 * "New rule" for the Workflows list, once the Rules tab is gone (spec: D7 is "done automatically
 * when an action-only rule is created"). The Rules tab's own NewRuleForm ("When does this
 * happen?" / "Then what happens?") creates the rule with `POST /rules` exactly as the Rules tab
 * did, then D7 runs at once through the fold writes: a stepless workflow of its own, the rule
 * pointed at it. A failed second write, or an orphan wrapper, is shown with the same fix actions
 * as the D7 offer on an existing rule; the rule itself is kept either way.
 */
export function NewRule({ hrefFor = (id) => `/workflows?id=${encodeURIComponent(id)}`, onCreated, onCancel }: Readonly<NewRuleProps>) {
  const [known, setKnown] = useState<Known | null>(null);
  const [created, setCreated] = useState<Rule | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    void Promise.allSettled([listActors(controller.signal), listRules(controller.signal), listWorkflows(controller.signal)]).then(
      ([actors, rules, workflows]) => {
        if (controller.signal.aborted) return;
        setKnown({
          actors: actors.status === "fulfilled" ? actors.value : [],
          ruleIds: rules.status === "fulfilled" ? rules.value.map((r) => r.id) : [],
          workflowIds: workflows.status === "fulfilled" ? workflows.value.map((w) => w.id) : [],
        });
      },
    );
    return () => controller.abort();
  }, []);

  // One create-then-D7 transaction at a time: while `POST /rules` is in flight the form is
  // disabled (a second submit cannot create a second rule) and cancel waits; once the rule exists
  // the D7 offer runs its writes at once and the form is gone.
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);
  const onCreate = async (doc: Rule) => {
    if (inFlight.current) return false;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      setCreated(await createRule(doc));
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  };
  const cancel = () => {
    if (!inFlight.current) onCancel();
  };

  return (
    <section className="fold-new-rule" aria-label="New rule">
      {error ? (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      ) : null}
      {created ? (
        <D7Offer rule={created} takenIds={known?.workflowIds ?? []} hrefFor={hrefFor} onCreated={onCreated} autoStart />
      ) : (
        <fieldset className="plain-group" disabled={pending} aria-busy={pending}>
          <NewRuleForm actors={known?.actors ?? []} takenIds={known?.ruleIds ?? []} onCancel={cancel} onCreate={onCreate} />
        </fieldset>
      )}
    </section>
  );
}

export default NewRule;
