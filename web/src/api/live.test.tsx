import { act, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FakeEventSource } from "../test/fakeEventSource";
import {
  LIVE_DEBOUNCE_MS,
  liveUrl,
  setLiveSourceFactory,
  useLiveUpdates,
  type LiveChange,
} from "./live";

function Probe({
  collections,
  onChange,
  onState,
}: {
  collections: string[];
  onChange: (c: LiveChange[]) => void;
  onState?: (s: { connected: boolean; flash: boolean }) => void;
}) {
  const state = useLiveUpdates(collections, onChange);
  onState?.(state);
  return <span data-testid="probe" data-flash={state.flash} data-connected={state.connected} />;
}

function reducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn().mockImplementation((query: string) => ({
      matches: reduce && query.includes("reduce"),
      media: query,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
    })),
  );
  window.matchMedia = globalThis.matchMedia;
}

describe("useLiveUpdates", () => {
  beforeEach(() => {
    FakeEventSource.reset();
    vi.useFakeTimers();
  });
  afterEach(() => {
    setLiveSourceFactory(undefined);
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("is off under tests unless an EventSource factory is injected", () => {
    const onChange = vi.fn();
    const { getByTestId } = render(<Probe collections={["rules"]} onChange={onChange} />);
    expect(FakeEventSource.instances).toHaveLength(0);
    expect(getByTestId("probe")).toHaveAttribute("data-connected", "false");
  });

  it("subscribes to /api/events/stream for the named collections", () => {
    setLiveSourceFactory(FakeEventSource.factory);
    render(<Probe collections={["rules", "runs"]} onChange={vi.fn()} />);
    expect(FakeEventSource.latest().url).toBe("/api/events/stream?collections=rules%2Cruns");
    expect(liveUrl(["a"], '{"a":"t1"}')).toBe(
      "/api/events/stream?collections=a&after=%7B%22a%22%3A%22t1%22%7D",
    );
  });

  it("hands a batch of change events to onChange, coalesced", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    const onChange = vi.fn();
    const { getByTestId } = render(<Probe collections={["rules"]} onChange={onChange} />);
    const es = FakeEventSource.latest();
    act(() => es.open());
    expect(getByTestId("probe")).toHaveAttribute("data-connected", "true");
    act(() => {
      es.change("rules", "r1");
      es.change("rules", "r2");
    });
    expect(onChange).not.toHaveBeenCalled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_DEBOUNCE_MS + 1);
    });
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].map((c: LiveChange) => c.id)).toEqual(["r1", "r2"]);
    expect(onChange.mock.calls[0][0][0]).toMatchObject({ collection: "rules", op: "update" });
  });

  it("reconnects after a fatal error, resuming after the last event id", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    render(<Probe collections={["rules"]} onChange={vi.fn()} />);
    const first = FakeEventSource.latest();
    act(() => {
      first.open();
      first.change("rules", "r1", '{"rules":"tok-9"}');
    });
    act(() => first.fail(true));
    expect(first.closed).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(FakeEventSource.instances).toHaveLength(2);
    expect(FakeEventSource.latest().url).toBe(
      `/api/events/stream?collections=rules&after=${encodeURIComponent('{"rules":"tok-9"}')}`,
    );
  });

  it("leaves a retrying (non-fatal) error to the browser's own reconnect", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    render(<Probe collections={["rules"]} onChange={vi.fn()} />);
    act(() => FakeEventSource.latest().fail(false));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it("closes the stream on unmount", () => {
    setLiveSourceFactory(FakeEventSource.factory);
    const { unmount } = render(<Probe collections={["rules"]} onChange={vi.fn()} />);
    unmount();
    expect(FakeEventSource.latest().closed).toBe(true);
  });

  it("flashes on a change, but never under prefers-reduced-motion", async () => {
    setLiveSourceFactory(FakeEventSource.factory);
    reducedMotion(false);
    const { getByTestId, unmount } = render(<Probe collections={["rules"]} onChange={vi.fn()} />);
    act(() => FakeEventSource.latest().change("rules", "r1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_DEBOUNCE_MS + 1);
    });
    expect(getByTestId("probe")).toHaveAttribute("data-flash", "true");
    unmount();

    reducedMotion(true);
    const again = render(<Probe collections={["rules"]} onChange={vi.fn()} />);
    act(() => FakeEventSource.latest().change("rules", "r1"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(LIVE_DEBOUNCE_MS + 1);
    });
    expect(again.getByTestId("probe")).toHaveAttribute("data-flash", "false");
  });
});
