import type { LiveSource } from "../api/live";

type Listener = (event: MessageEvent | Event) => void;

/**
 * A scripted EventSource for vitest: `useLiveUpdates` is handed
 * `FakeEventSource.factory` (via `setLiveSourceFactory`), and a test drives
 * it with `open()`, `change(...)` and `fail(...)`. Every instance is kept in
 * `FakeEventSource.instances`, newest last.
 */
export class FakeEventSource implements LiveSource {
  static instances: FakeEventSource[] = [];

  static factory = (url: string): LiveSource => new FakeEventSource(url);

  static reset() {
    FakeEventSource.instances = [];
  }

  static latest(): FakeEventSource {
    const last = FakeEventSource.instances[FakeEventSource.instances.length - 1];
    if (!last) throw new Error("no EventSource was opened");
    return last;
  }

  readonly url: string;
  readyState = 0;
  closed = false;
  private listeners: Record<string, Listener[]> = {};

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, fn: Listener) {
    (this.listeners[type] ??= []).push(fn);
  }

  removeEventListener(type: string, fn: Listener) {
    this.listeners[type] = (this.listeners[type] ?? []).filter((l) => l !== fn);
  }

  close() {
    this.closed = true;
    this.readyState = 2;
  }

  private dispatch(type: string, event: MessageEvent | Event) {
    for (const fn of this.listeners[type] ?? []) fn(event);
  }

  open() {
    this.readyState = 1;
    this.dispatch("open", new Event("open"));
  }

  /** One `change` frame, as culture_rules/server/events.py emits it. */
  change(collection: string, id: string, lastEventId = `{"${collection}":"t-${id}"}`, op = "update") {
    this.dispatch(
      "change",
      new MessageEvent("change", {
        data: JSON.stringify({ collection, op, id, document: { id } }),
        lastEventId,
      }),
    );
  }

  /** The stream failed: `fatal` closes it (no browser retry), else the browser retries. */
  fail(fatal = true) {
    this.readyState = fatal ? 2 : 0;
    this.dispatch("error", new Event("error"));
  }
}
