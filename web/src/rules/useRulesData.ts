import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, listMachines, listRules, listWorkflows } from "../api/client";
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

const describe = (err: unknown) => (err instanceof ApiError ? err.message : String(err));

interface Loaded {
  rules: RuleDoc[];
  machines: Machine[];
  workflows: Workflow[];
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

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([
      listRules(controller.signal),
      listMachines(controller.signal),
      listWorkflows(controller.signal),
    ]).then((results) => {
      if (controller.signal.aborted) return;
      const [rules, machines, workflows] = results;
      setLoaded({
        rules: rules.status === "fulfilled" ? rules.value : [],
        machines: machines.status === "fulfilled" ? machines.value : [],
        workflows: workflows.status === "fulfilled" ? workflows.value : [],
        errors: results
          .map((r) => (r.status === "rejected" ? describe(r.reason) : null))
          .filter((m): m is string => m !== null),
      });
    });
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

  const toggle = useCallback(
    async (rule: RuleDoc) => {
      const next = !(rule.enabled !== false);
      replace({ ...rule, enabled: next }); // optimistic; rolled back below on refusal
      const doc = await attempt(() => setRuleEnabled(rule, next));
      replace(doc ?? rule);
    },
    [attempt, replace],
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
    (async () => {
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
  }, [selectedId]);

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
    loadErrors: loaded?.errors ?? [],
    notice,
    clearNotice: () => setNotice(null),
    setNotice,
    toggle,
    save,
    create,
    remove,
    restore,
    asks: asks && asks.ruleId === selectedId ? asks : null,
    answer,
  };
}
