/// <reference types="vite/client" />
import { useEffect, useRef, useState } from "react";
import { API_ROOT } from "./client";

/**
 * Live editor updates over the API's server-sent events
 * (`GET /events/stream`, culture_rules/server/events.py): one `change` frame
 * per committed write, `{collection, op, id, document}`, whose SSE id is the
 * cursor map a reconnect resumes from.
 *
 * `useLiveUpdates(collections, onChange)` keeps one EventSource open for the
 * mounted view and hands `onChange` each coalesced batch of changes; the view
 * refetches what it shows. It is a nudge, not the data: every tab still reads
 * its state through the ordinary API calls.
 *
 * - **Reconnect:** a stream the browser retries by itself (it ended, or the
 *   network blipped) resumes with the `Last-Event-ID` header. A stream the
 *   browser gave up on (non-200, wrong type) is reopened here with backoff
 *   and `?after=<last event id>`, so no committed change is skipped.
 * - **Tests:** off unless an EventSource factory is injected
 *   (`setLiveSourceFactory`); under vitest the default is no stream at all.
 * - **Motion:** `flash` is a short "just changed" signal for a visual cue. It
 *   stays false under `prefers-reduced-motion: reduce`.
 */

export interface LiveChange {
  collection: string;
  op: string;
  id: string;
  document?: unknown;
}

/** The part of `EventSource` the hook uses (so tests can script one). */
export interface LiveSource {
  readonly url: string;
  readonly readyState: number;
  addEventListener(type: string, fn: (event: MessageEvent | Event) => void): void;
  close(): void;
}

export type LiveSourceFactory = (url: string) => LiveSource;

/** Changes arriving within this window reach `onChange` as one batch. */
export const LIVE_DEBOUNCE_MS = 200;
/** How long `flash` stays true after a batch. */
export const LIVE_FLASH_MS = 700;
const RETRY_MIN_MS = 1_000;
const RETRY_MAX_MS = 30_000;
const CLOSED = 2;

let injected: LiveSourceFactory | null | undefined;

/**
 * Inject the EventSource factory (tests, embedding). `undefined` restores the
 * default (the browser's EventSource, none under vitest); `null` turns live
 * updates off.
 */
export function setLiveSourceFactory(factory: LiveSourceFactory | null | undefined) {
  injected = factory;
}

function sourceFactory(): LiveSourceFactory | null {
  if (injected !== undefined) return injected;
  if (import.meta.env.MODE === "test") return null;
  if (typeof EventSource === "undefined") return null;
  return (url) => new EventSource(url);
}

/** `/api/events/stream?collections=a,b[&after=<cursor map>]`. */
export function liveUrl(collections: readonly string[], after?: string | null): string {
  const search = new URLSearchParams({ collections: collections.join(",") });
  if (after) search.set("after", after);
  return `${API_ROOT}/events/stream?${search.toString()}`;
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
  } catch {
    return false;
  }
}

function parse(data: unknown): LiveChange | null {
  if (typeof data !== "string") return null;
  try {
    const body = JSON.parse(data) as Partial<LiveChange>;
    return typeof body.collection === "string" && typeof body.id === "string"
      ? { collection: body.collection, op: String(body.op ?? "update"), id: body.id, document: body.document }
      : null;
  } catch {
    return null;
  }
}

export function useLiveUpdates(
  collections: readonly string[],
  onChange: (changes: LiveChange[]) => void,
): { connected: boolean; flash: boolean } {
  const [connected, setConnected] = useState(false);
  const [flash, setFlash] = useState(false);
  const handler = useRef(onChange);
  handler.current = onChange;
  const key = collections.join(",");

  useEffect(() => {
    const factory = sourceFactory();
    if (!factory || !key) return;
    const names = key.split(",");
    let source: LiveSource | null = null;
    let lastId: string | null = null;
    let pending: LiveChange[] = [];
    let flushTimer: ReturnType<typeof setTimeout> | undefined;
    let flashTimer: ReturnType<typeof setTimeout> | undefined;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let backoff = RETRY_MIN_MS;
    let stopped = false;

    const flush = () => {
      flushTimer = undefined;
      const batch = pending;
      pending = [];
      if (batch.length === 0 || stopped) return;
      if (!prefersReducedMotion()) {
        setFlash(true);
        if (flashTimer) clearTimeout(flashTimer);
        flashTimer = setTimeout(() => setFlash(false), LIVE_FLASH_MS);
      }
      handler.current(batch);
    };

    const open = () => {
      retryTimer = undefined;
      if (stopped) return;
      const es = factory(liveUrl(names, lastId));
      source = es;
      es.addEventListener("open", () => {
        backoff = RETRY_MIN_MS;
        setConnected(true);
      });
      es.addEventListener("change", (event) => {
        const message = event as MessageEvent;
        if (message.lastEventId) lastId = message.lastEventId;
        const change = parse(message.data);
        if (!change) return;
        pending.push(change);
        flushTimer ??= setTimeout(flush, LIVE_DEBOUNCE_MS);
      });
      es.addEventListener("error", () => {
        setConnected(false);
        if (es.readyState !== CLOSED || stopped) return; // the browser retries by itself
        es.close();
        retryTimer = setTimeout(open, backoff);
        backoff = Math.min(backoff * 2, RETRY_MAX_MS);
      });
    };

    open();
    return () => {
      stopped = true;
      source?.close();
      for (const t of [flushTimer, flashTimer, retryTimer]) if (t) clearTimeout(t);
      setConnected(false);
      setFlash(false);
    };
  }, [key]);

  return { connected, flash };
}
