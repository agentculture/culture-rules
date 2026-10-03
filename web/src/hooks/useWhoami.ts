import { useEffect, useSyncExternalStore } from "react";
import { ApiError, getWhoami } from "../api/client";
import type { Role, Whoami } from "../api/types";
import { setAgentState } from "../agent-state/store";

/**
 * The browser's identity, mirrored from culture-nodes' useWhoami.
 *
 * Identity is DERIVED, never typed: the Cloudflare Access login rides every
 * same-origin request, and `GET /whoami` says who the API verified. Read ONCE
 * per page session and shared with every component that asks. There is no
 * login form and no token field anywhere in the app.
 *
 * States are distinct so no view mistakes one for another: `unauthenticated`
 * is a 401 (no identity reached the API); `unavailable` is any other failure,
 * which says nothing about who is here and must not render as "signed out".
 * No identity is ever invented: a missing route is `unavailable`.
 */
export type WhoamiState =
  | { status: "loading" }
  | {
      status: "signed-in";
      whoami: Whoami;
      displayName: string;
      kind: Whoami["kind"];
      /** The highest of `whoami.roles`; null when no known role is held. */
      role: Role | null;
    }
  | { status: "unauthenticated" }
  | { status: "unavailable"; error: ApiError };

const ROLE_ORDER: Role[] = ["viewer", "editor", "admin"];

/** The effective role: the highest of `roles` (viewer < editor < admin). */
export function effectiveRole(roles: readonly string[]): Role | null {
  let best = -1;
  for (const role of roles) best = Math.max(best, ROLE_ORDER.indexOf(role as Role));
  return best >= 0 ? ROLE_ORDER[best] : null;
}

const LOADING: WhoamiState = { status: "loading" };
let state: WhoamiState = LOADING;
let started = false;
const listeners = new Set<() => void>();

function publish(next: WhoamiState) {
  state = next;
  setAgentState({
    identity: {
      status: next.status,
      identity: next.status === "signed-in" ? next.whoami.identity : null,
      kind: next.status === "signed-in" ? next.kind : null,
      role: next.status === "signed-in" ? next.role : null,
    },
  });
  for (const listener of listeners) listener();
}

function start() {
  if (started) return;
  started = true;
  publish(LOADING);
  getWhoami()
    .then((whoami) =>
      publish({
        status: "signed-in",
        whoami,
        displayName: whoami.identity,
        kind: whoami.kind,
        role: effectiveRole(whoami.roles ?? []),
      }),
    )
    .catch((cause: unknown) => {
      const error =
        cause instanceof ApiError ? cause : new ApiError(0, "unknown", String(cause));
      publish(error.status === 401 ? { status: "unauthenticated" } : { status: "unavailable", error });
    });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const snapshot = () => state;

/** The signed-in principal, read once per session and shared. */
export function useWhoami(): WhoamiState {
  const current = useSyncExternalStore(subscribe, snapshot, snapshot);
  useEffect(() => {
    start();
  }, []);
  return current;
}

/** Forget the session's answer so the next mount fetches again (tests only). */
export function resetWhoamiForTests(): void {
  state = LOADING;
  started = false;
  listeners.clear();
}
