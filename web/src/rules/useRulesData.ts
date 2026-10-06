import { useCallback, useEffect, useMemo, useState } from "react";
import { listActors, type Actor } from "../api/actors";
import { ApiError, listMachines, listRules, listWorkflows } from "../api/client";
import { failureMessage, settleAll } from "../api/settle";
import {
  answerAsk,
  createRule,
  deleteRule,
  listAsks,
  listWaitingRuns,
  restoreRule,
  setRuleEnabled,
  updateRule,
  type Ask,
  type RuleDoc,
} from "../api/rules";
import type { Machine, Workflow } from "../api/types";
import { guidanceFor } from "../api/guidance";
import { usePending } from "../usePending";

/** Never throws, so the handlers below that describe a failure cannot fail themselves. */
/** The variable refusals the editor explains in its own words (api/guidance.ts). */
const VARIABLE_CODES = ["variable_undefined", "variables_unsupported_nodes"];

function describe(err: unknown): string {
  if (!(err instanceof ApiError)) return failureMessage(err);
  const hit =
    VARIABLE_CODES.find((c) => c === err.code) ?? err.errors.find((e) => VARIABLE_CODES.includes(e.code));
  if (!hit) return err.message;
  const code = typeof hit === "string" ? hit : hit.code;
  // The nested message names the variable or the nodes at fault; it rides after the plain words.
  const detail = typeof hit === "string" ? "" : ` (${hit.message})`;
  return `${guidanceFor(code).message}${detail}`;
}

interface Loaded {
  rules: RuleDoc[];
  machines: Machine[];
  workflows: Workflow[];
  actors: Actor[];
  errors: string[];
}

/**
 * The Rules tab's data and verbs. The list is the single source: every write
 * answers the stored rule, which replaces the one in the list. A refused
 * write leaves the list as it was and sets `notice` (shown as an alert).
 */
export function useRulesData(routeRuleId: string | undefined) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Bumped by live updates: `rulesTick` refetches the list, `asksTick` the asks.
  const [rulesTick, setRulesTick] = useState(0);
  const [asksTick, setAsksTick] = useState(0);
  const refreshRules = useCallback(() => setRulesTick((n) => n + 1), []);
  const refreshAsks = useCallback(() => setAsksTick((n) => n + 1), []);

  useEffect(() => {
    if (rulesTick === 0) return;
    const controller = new AbortController();
    listRules(controller.signal)
      .then((rules) => {
        if (!controller.signal.aborted) setLoaded((l) => (l ? { ...l, rules } : l));
      })
      .catch(() => {
        // A failed live refresh keeps what is shown; the next change retries.
      });
    return () => controller.abort();
  }, [rulesTick]);

  useEffect(() => {
    const controller = new AbortController();
    settleAll(
      [
        listRules(controller.signal),
        listMachines(controller.signal),
        listWorkflows(controller.signal),
        listActors(controller.signal),
      ],
      (results) => {
        if (controller.signal.aborted) return;
        const [rules, machines, workflows, actors] = results;
        setLoaded({
          rules: rules.status === "fulfilled" ? rules.value : [],
          machines: machines.status === "fulfilled" ? machines.value : [],
          workflows: workflows.status === "fulfilled" ? workflows.value : [],
          actors: actors.status === "fulfilled" ? actors.value : [],
          errors: results
            .map((r) => (r.status === "rejected" ? describe(r.reason) : null))
            .filter((m): m is string => m !== null),
        });
      },
      (message) => {
        // Applying the load failed: an empty list with the failure named.
        if (controller.signal.aborted) return;
        setLoaded({ rules: [], machines: [], workflows: [], actors: [], errors: [message] });
      },
    );
    return () => controller.abort();
  }, []);

  const rules = useMemo(() => loaded?.rules ?? [], [loaded]);
  const selected = rules.find((r) => r.id === routeRuleId) ?? rules[0] ?? null;
  const selectedId = selected?.id;

  const replace = useCallback((doc: RuleDoc) => {
    setLoaded((l) =>
      l ? { ...l, rules: l.rules.map((r) => (r.id === doc.id ? { ...r, ...doc } : r)) } : l,
    );
  }, []);

  /** Run a write; a failure becomes the notice and resolves to null. */
  const attempt = useCallback(async <T,>(write: () => Promise<T>): Promise<T | null> => {
    setNotice(null);
    try {
      return await write();
    } catch (err) {
      setNotice(describe(err));
      return null;
    }
  }, []);

  const { pending: togglePending, run: runToggle } = usePending();
  const toggle = useCallback(
    (rule: RuleDoc) =>
      runToggle(rule.id, async () => {
        const next = rule.enabled === false;
        replace({ ...rule, enabled: next }); // optimistic; rolled back below on refusal
        const doc = await attempt(() => setRuleEnabled(rule, next));
        replace(doc ?? rule);
      }),
    [attempt, replace, runToggle],
  );

  const save = useCallback(
    async (rule: RuleDoc): Promise<boolean> => {
      const doc = await attempt(() => updateRule(rule));
      if (doc) replace(doc);
      return doc !== null;
    },
    [attempt, replace],
  );

  const create = useCallback(
    async (rule: RuleDoc): Promise<RuleDoc | null> => {
      const doc = await attempt(() => createRule(rule));
      if (doc) setLoaded((l) => (l ? { ...l, rules: [...l.rules, doc] } : l));
      return doc;
    },
    [attempt],
  );

  const remove = useCallback(
    async (rule: RuleDoc): Promise<boolean> => {
      const done = await attempt(() => deleteRule(rule.id));
      if (done === null) return false;
      setLoaded((l) => (l ? { ...l, rules: l.rules.filter((r) => r.id !== rule.id) } : l));
      return true;
    },
    [attempt],
  );

  const restore = useCallback(
    async (rule: RuleDoc): Promise<RuleDoc | null> => {
      const doc = await attempt(() => restoreRule(rule));
      if (doc) setLoaded((l) => (l ? { ...l, rules: [...l.rules, doc] } : l));
      return doc;
    },
    [attempt],
  );

  // ---- pending human asks of the selected rule's waiting runs
  const [asks, setAsks] = useState<{ ruleId: string; items: Ask[]; error: string | null } | null>(
    null,
  );
  useEffect(() => {
    if (!selectedId) return;
    const controller = new AbortController();
    // `void` is honest here: the body is one try/catch whose handler only sets state
    // with a message from `describe` (which never throws), so this promise cannot reject.
    void (async () => {
      try {
        const runs = await listWaitingRuns(selectedId, controller.signal);
        const lists = await Promise.all(runs.map((r) => listAsks(r.id, controller.signal)));
        if (!controller.signal.aborted)
          setAsks({ ruleId: selectedId, items: lists.flat(), error: null });
      } catch (err) {
        if (!controller.signal.aborted)
          setAsks({ ruleId: selectedId, items: [], error: describe(err) });
      }
    })();
    return () => controller.abort();
  }, [selectedId, asksTick]);

  const answer = useCallback(
    async (ask: Ask, value: string) => {
      const done = await attempt(() => answerAsk(ask.id, value));
      if (done === null) return false;
      setAsks((a) => (a ? { ...a, items: a.items.filter((x) => x.id !== ask.id) } : a));
      return true;
    },
    [attempt],
  );

  return {
    loaded,
    rules,
    selected,
    machines: loaded?.machines ?? [],
    workflows: loaded?.workflows ?? [],
    actors: loaded?.actors ?? [],
    loadErrors: loaded?.errors ?? [],
    notice,
    clearNotice: () => setNotice(null),
    setNotice,
    toggle,
    togglePending,
    save,
    create,
    remove,
    restore,
    asks: asks?.ruleId === selectedId ? asks : null,
    answer,
    refreshRules,
    refreshAsks,
  };
}
