import { useEffect, useSyncExternalStore } from "react";
import { ApiError, getWhoami } from "../api/client";
import type { Whoami } from "../api/types";
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
 * `signed-in` with `mocked: true` is the stand-in used while the API has no
 * /whoami route (api/client.ts `MOCK_WHOAMI`).
 */
export type WhoamiState =
  | { status: "loading" }
  | { status: "signed-in"; whoami: Whoami; displayName: string; mocked: boolean }
  | { status: "unauthenticated" }
  | { status: "unavailable"; error: ApiError };

export function displayNameOf(whoami: Whoami): string {
  return whoami.display_name || whoami.email || whoami.subject;
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
      subject: next.status === "signed-in" ? next.whoami.subject : null,
      role: next.status === "signed-in" ? next.whoami.role : null,
      mocked: next.status === "signed-in" ? next.mocked : false,
    },
  });
  for (const listener of listeners) listener();
}

function start() {
  if (started) return;
  started = true;
  publish(LOADING);
  getWhoami()
    .then(({ whoami, mocked }) =>
      publish({ status: "signed-in", whoami, displayName: displayNameOf(whoami), mocked }),
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
