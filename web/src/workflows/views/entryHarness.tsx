/**
 * Shared set-up for the EntryEditors tests ported from the deleted Simple view tests
 * (deviation d9, task t22): the fold fixture in the fake API, a render of EntryEditors
 * (what Detailed mounts) and the lookups those tests share.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import type userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { expect, vi } from "vitest";
import type { LiveChange } from "../../api/live";
import type { Rule } from "../../api/types";
import type { WorkflowDef } from "../../api/workflows";
import { createFakeApi, fetchFor, handle, type FakeApi } from "../../rules/fake-api";
import { FOLD_RULES, FOLD_WORKFLOWS } from "../simple/fixture";
import type { LiveFeed } from "../simple/liveFeed";
import EntryEditors from "./EntryEditors";

export type User = ReturnType<typeof userEvent.setup>;

export const harness: { api: FakeApi } = { api: undefined as unknown as FakeApi };

export const FOLD_NOW = Date.parse("2026-10-09T12:00:00Z");
export const failure = { status: 503, code: "store_down", message: "store unreachable" };

/** The fold fixture in the fake API, listed once so every rule carries its `updated_at`. */
export function foldApi(edit?: (rules: Rule[]) => void): FakeApi {
  const fake = createFakeApi(FOLD_NOW);
  fake.rules = structuredClone(FOLD_RULES);
  edit?.(fake.rules);
  fake.workflows = structuredClone(FOLD_WORKFLOWS);
  handle(fake, "GET", "/rules", new URLSearchParams());
  fake.calls = [];
  return fake;
}

export function use(fake: FakeApi) {
  harness.api = fake;
  vi.stubGlobal("fetch", fetchFor(fake));
}

export interface RenderProps {
  workflowId?: string;
  def?: WorkflowDef;
  /** The entry point asked for (`?entry=`). */
  entry?: string | null;
  /** A live feed the test emits into, as the Workflows tab hands one down. */
  live?: LiveFeed;
}

export function defFor(workflowId: string, def?: WorkflowDef): WorkflowDef {
  return def ?? FOLD_WORKFLOWS.find((w) => w.id === workflowId) ?? ({ id: workflowId, name: workflowId, steps: [], edges: [] } as WorkflowDef);
}

export function renderEditors(props: RenderProps = {}) {
  const workflow = defFor(props.workflowId ?? "pr-fix", props.def);
  return render(
    <MemoryRouter>
      <EntryEditors workflow={workflow} entry={props.entry} live={props.live} />
    </MemoryRouter>,
  );
}

export const api = () => harness.api;
export const writes = () => harness.api.calls.filter((c) => c.method !== "GET");
export const sent = (method: string, path: string) => harness.api.calls.filter((c) => c.method === method && c.path === path);
export const entry = (name: string) => screen.getByRole("group", { name: `Entry point: ${name}` });
export const findEntry = (name: string) => screen.findByRole("group", { name: `Entry point: ${name}` });
export const thenGroup = (name: string) => screen.getByRole("group", { name });

/** Detailed opens no entry point by itself: click the card's Expand if it is collapsed. */
export async function openEntry(user: User, name: string) {
  const group = await findEntry(name);
  const expand = within(group).queryByRole("button", { name: `Expand ${name}` });
  if (expand) await user.click(expand);
  await waitFor(() => expect(within(entry(name)).getByRole("button", { name: `Collapse ${name}` })).toBeInTheDocument());
  return entry(name);
}

export async function editRunKey(user: User, key: string) {
  await findEntry("Checks settled, not green");
  await user.click(within(thenGroup("Runs")).getByRole("button", { name: "Edit runs" }));
  const form = screen.getByRole("form", { name: "Runs" });
  await user.clear(within(form).getByLabelText("Run key"));
  await user.type(within(form).getByLabelText("Run key"), key.replaceAll("{", "{{"));
  await user.click(within(form).getByRole("button", { name: "Save for every entry point" }));
}

/** A live feed the test emits into, as the Workflows tab hands one down. */
export function testFeed(): LiveFeed {
  const listeners = new Set<(changes: LiveChange[]) => void>();
  return {
    subscribe: (l) => {
      listeners.add(l);
      return () => {
        listeners.delete(l);
      };
    },
    emit: (changes) => listeners.forEach((l) => l(changes)),
  };
}

/** Another writer changes rules on the server; the change arrives through the feed and is re-listed. */
export async function concurrent(feed: LiveFeed, ids: string[], change: Record<string, unknown>, at = "2026-10-09T13:30:00Z") {
  for (const id of ids) Object.assign(harness.api.rules.find((r) => r.id === id)!, structuredClone(change), { updated_at: at });
  const before = sent("GET", "/rules").length;
  feed.emit([{ collection: "rules", op: "update", id: ids[0] } as LiveChange]);
  await waitFor(() => expect(sent("GET", "/rules").length).toBeGreaterThan(before));
}
