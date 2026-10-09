/**
 * The Debug view (canvas WF-Variables, WF-Variables-Port): every input and
 * output port of a workflow as a row — name, type, and the reference it reads
 * (`← steps.fetch-diff.outputs.diff`) — in cards laid out in columns by data
 * depth, `in` first and `out` last, loop bodies inside their loop's card.
 *
 * Its own layout, not React Flow: it is read, not edited. Each port is a
 * button; choosing one lights it, its upstream and its downstream (./ports.ts
 * portLinks) and dims the rest, and opens the "Selected port" panel with the
 * reference, Copy reference, Show everything downstream and the two lists.
 * Escape, Close or choosing the port again clears the selection; Close and
 * Escape hand focus back to the port.
 *
 * The wires are an SVG under the cards, measured from the port rows after
 * layout; they are decoration (aria-hidden): every link is also a row's text.
 */
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import type { WorkflowDef } from "../../api/workflows";
import { placementLabel } from "../model";
import {
  allLinks,
  debugColumns,
  debugPorts,
  portLinks,
  unboundRequired,
  type DebugGroup,
  type Carried,
  type DebugPort,
  type PortLink,
} from "./ports";

export interface DebugViewProps {
  workflow: WorkflowDef;
}

type Related = "selected" | "upstream" | "downstream" | "none";

interface Wire {
  id: string;
  d: string;
  kind: PortLink["kind"];
  conditional: boolean;
  from: string;
  to: string;
}

/** "if gate runs": a carried value from a body step with a `config.when`. */
const ifRuns = (c: Carried) => (c.conditional ? ` if ${c.ref.split(".")[1]} runs` : "");

function portLabel(p: DebugPort): string {
  const bits = [p.ref, p.type];
  if (!p.required) bits.push("optional");
  if (p.reads) bits.push(`reads ${p.reads}${p.byName ? " by name" : ""}`);
  else if (p.side === "in") bits.push("not wired");
  if (p.fallback) bits.push(`else ${p.fallback} if the wire supplies nothing`);
  for (const c of p.carried) bits.push(`then carried from ${c.ref}${ifRuns(c)}`);
  if (p.exported) bits.push("exported to out");
  return bits.join(", ");
}

function PortRow({
  port,
  related,
  onChoose,
}: Readonly<{ port: DebugPort; related: Related; onChoose: (ref: string) => void }>) {
  return (
    <li className="wf-debug-port__item">
      <button
        type="button"
        className={`wf-debug-port wf-debug-port--${port.side}`}
        data-ref={port.ref}
        data-related={related}
        data-required={port.required ? "true" : "false"}
        data-unbound={port.side === "in" && port.required && port.reads === null ? "true" : undefined}
        aria-pressed={related === "selected"}
        aria-label={portLabel(port)}
        onClick={() => onChoose(port.ref)}
      >
        <span className="wf-debug-port__dot" aria-hidden="true" />
        <span className="wf-debug-port__line">
          <span className="wf-debug-port__name">{port.name}</span>
          <span className="wf-debug-port__type">{port.type}</span>
          {port.exported ? <span className="wf-debug-port__badge">→ out</span> : null}
        </span>
        {port.reads ? (
          <span className="wf-debug-port__reads">
            ← {port.reads}
            {port.byName ? " · by name" : ""}
          </span>
        ) : null}
        {port.fallback ? (
          <span className="wf-debug-port__reads wf-debug-port__reads--fallback">else ← {port.fallback}</span>
        ) : null}
        {port.carried.map((c) => (
          <span key={c.ref} className="wf-debug-port__reads wf-debug-port__reads--carry">
            ↻ {c.ref}
            {c.conditional ? ` ·${ifRuns(c)}` : ""}
          </span>
        ))}
      </button>
    </li>
  );
}

function groupSubtitle(g: DebugGroup): string | null {
  if (!g.step) return null;
  const bits: string[] = [g.step.id, g.step.kind];
  if (g.step.max_iterations) bits.push(`max ${g.step.max_iterations}`);
  return bits.join(" · ");
}

function DebugCard({
  group,
  relatedOf,
  onChoose,
}: Readonly<{ group: DebugGroup; relatedOf: (ref: string) => Related; onChoose: (ref: string) => void }>) {
  const ports = (list: DebugPort[], side: "in" | "out") =>
    list.length ? (
      <ul className={`wf-debug-card__ports wf-debug-card__ports--${side}`}>
        {list.map((p) => (
          <PortRow key={p.ref} port={p} related={relatedOf(p.ref)} onChoose={onChoose} />
        ))}
      </ul>
    ) : null;
  const subtitle = groupSubtitle(group);
  return (
    <div
      role="group"
      aria-label={group.label}
      className={`wf-debug-card wf-debug-card--${group.kind}${group.body.length ? " wf-debug-card--loop" : ""}`}
    >
      <div className="wf-debug-card__head">
        {group.step ? <span className="wf-debug-card__host">{placementLabel(group.step.placement)}</span> : null}
        <span className="wf-debug-card__title">{group.label}</span>
        {subtitle ? <span className="wf-debug-card__subtitle">{subtitle}</span> : null}
      </div>
      {ports(group.inputs, "in")}
      {group.body.length ? (
        <div className="wf-debug-card__body">
          {group.body.map((inner) => (
            <DebugCard key={inner.id} group={inner} relatedOf={relatedOf} onChoose={onChoose} />
          ))}
        </div>
      ) : null}
      {ports(group.outputs, "out")}
    </div>
  );
}

function RefList({
  label,
  refs,
  ports,
  arrow,
}: Readonly<{ label: string; refs: ReadonlySet<string>; ports: ReadonlyMap<string, DebugPort>; arrow: "←" | "→" }>) {
  return (
    <div className="wf-debug-selected__column">
      <span className="wf-debug-selected__label" id={`wf-debug-${label.toLowerCase()}`}>
        {label}
      </span>
      {refs.size === 0 ? (
        <p className="wf-debug-selected__none">Nothing</p>
      ) : (
        <ul className="wf-debug-selected__list" aria-labelledby={`wf-debug-${label.toLowerCase()}`}>
          {[...refs].map((ref) => {
            const reads = arrow === "←" ? ports.get(ref)?.reads : null;
            return (
              <li key={ref} className="wf-debug-selected__row">
                <code className="wf-debug-ref">{ref}</code>
                {reads ? (
                  <>
                    <span aria-hidden="true">←</span>
                    <span className="sr-only">reads</span>
                    <code className="wf-debug-ref">{reads}</code>
                  </>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function SelectedPort({
  port,
  upstream,
  downstream,
  ports,
  everything,
  onEverything,
  onClose,
}: Readonly<{
  port: DebugPort;
  upstream: ReadonlySet<string>;
  downstream: ReadonlySet<string>;
  ports: ReadonlyMap<string, DebugPort>;
  everything: boolean;
  onEverything: () => void;
  onClose: () => void;
}>) {
  const [copied, setCopied] = useState(false);
  useEffect(() => setCopied(false), [port.ref]);
  const copy = () => {
    try {
      navigator.clipboard.writeText(port.ref).then(
        () => setCopied(true),
        () => setCopied(false),
      );
    } catch {
      setCopied(false); // no clipboard here: the reference stays on screen to select
    }
  };
  return (
    <section className="wf-debug-selected" aria-label="Selected port">
      <div className="wf-debug-selected__head">
        <code className="wf-debug-selected__ref">{port.ref}</code>
        <span className="wf-debug-chip">{port.type}</span>
        <span className="wf-debug-chip">{port.required ? "required" : "optional"}</span>
        <span className="wf-debug-selected__spacer" />
        <span className="sr-only" aria-live="polite">
          {copied ? "Reference copied" : ""}
        </span>
        <button type="button" className="wf-button" onClick={copy}>
          {copied ? "Copied" : "Copy reference"}
        </button>
        <button type="button" className="wf-button" aria-pressed={everything} onClick={onEverything}>
          Show everything downstream
        </button>
        <button type="button" className="wf-debug-selected__close" aria-label="Close" onClick={onClose}>
          <span aria-hidden="true">×</span>
        </button>
      </div>
      <div className="wf-debug-selected__lists">
        <RefList label="Upstream" refs={upstream} ports={ports} arrow="←" />
        <RefList label="Downstream" refs={downstream} ports={ports} arrow="→" />
      </div>
    </section>
  );
}

/** The wires between port rows, measured against the grid after layout. */
function useWires(
  gridRef: RefObject<HTMLDivElement | null>,
  links: readonly PortLink[],
): Wire[] {
  const [wires, setWires] = useState<Wire[]>([]);
  const measure = useCallback(() => {
    const grid = gridRef.current;
    if (!grid) return;
    const box = grid.getBoundingClientRect();
    const rowOf = (ref: string) => grid.querySelector<HTMLElement>(`[data-ref="${CSS.escape(ref)}"]`);
    const next: Wire[] = [];
    for (const l of links) {
      if (l.kind === "step") continue;
      const a = rowOf(l.from)?.getBoundingClientRect();
      const b = rowOf(l.to)?.getBoundingClientRect();
      if (!a || !b || (a.width === 0 && b.width === 0)) continue;
      // An outer loop input feeding its own body runs left to right inside the card.
      const x1 = a.right - box.left;
      const y1 = a.top + a.height / 2 - box.top;
      const x2 = b.left - box.left;
      const y2 = b.top + b.height / 2 - box.top;
      const bend = Math.max(24, Math.abs(x2 - x1) / 2);
      next.push({
        id: `${l.from}->${l.to}`,
        d: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`,
        kind: l.kind,
        conditional: l.conditional ?? false,
        from: l.from,
        to: l.to,
      });
    }
    setWires((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
  }, [gridRef, links]);

  useLayoutEffect(() => {
    measure();
  }, [measure]);
  useEffect(() => {
    const grid = gridRef.current;
    if (!grid || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => measure());
    observer.observe(grid);
    return () => observer.disconnect();
  }, [gridRef, measure]);
  return wires;
}

export function DebugView({ workflow }: Readonly<DebugViewProps>) {
  const columns = useMemo(() => debugColumns(workflow), [workflow]);
  const ports = useMemo(() => new Map(debugPorts(workflow).map((p) => [p.ref, p])), [workflow]);
  const links = useMemo(() => allLinks(workflow), [workflow]);
  const unbound = useMemo(() => unboundRequired(workflow), [workflow]);

  const [selected, setSelected] = useState<string | null>(null);
  const [everything, setEverything] = useState(false);
  // A port that the edited workflow no longer has is no longer selected.
  const current = selected !== null && ports.has(selected) ? selected : null;

  const relations = useMemo(
    () => (current ? portLinks(workflow, current, { everything }) : null),
    [workflow, current, everything],
  );
  const relatedOf = useCallback(
    (ref: string): Related => {
      if (!relations) return "none";
      if (ref === current) return "selected";
      if (relations.upstream.has(ref)) return "upstream";
      if (relations.downstream.has(ref)) return "downstream";
      return "none";
    },
    [relations, current],
  );

  const choose = useCallback((ref: string) => {
    setSelected((prev) => (prev === ref ? null : ref));
    setEverything(false);
  }, []);
  const gridRef = useRef<HTMLDivElement | null>(null);
  /** Dismiss the panel and hand focus back to the port it was about. */
  const clear = () => {
    const ref = current;
    setSelected(null);
    setEverything(false);
    if (ref) gridRef.current?.querySelector<HTMLElement>(`[data-ref="${CSS.escape(ref)}"]`)?.focus();
  };
  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "Escape" && current) {
      e.stopPropagation();
      clear();
    }
  };

  const wires = useWires(gridRef, links);
  const lit = (w: Wire) => relatedOf(w.from) !== "none" && relatedOf(w.to) !== "none";

  const port = current ? ports.get(current) : undefined;
  return (
    // Escape is a convenience; Close and a second click on the port do the same.
    <section
      className="wf-debug"
      aria-label="Workflow ports"
      data-selection={current ? "true" : "false"}
      onKeyDown={onKeyDown}
    >
      <div className="wf-debug__status">
        {unbound.length === 0 ? (
          <span className="wf-debug-status wf-debug-status--ok">All required ports bound</span>
        ) : (
          <span className="wf-debug-status wf-debug-status--warn">
            {unbound.length === 1 ? "1 required port unbound" : `${unbound.length} required ports unbound`}
          </span>
        )}
      </div>
      <div className="wf-debug__scroll">
        <div className="wf-debug__grid" ref={gridRef}>
          <svg className="wf-debug__wires" aria-hidden="true" focusable="false">
            {wires.map((w) => {
              let state = "";
              if (current) state = lit(w) ? " is-lit" : " is-dim";
              return <path key={w.id} d={w.d} className={`wf-debug-wire wf-debug-wire--${w.kind}${w.conditional ? " is-conditional" : ""}${state}`} />;
            })}
          </svg>
          {columns.map((col) => (
            <div key={col.map((g) => g.id).join(",")} className="wf-debug__column">
              {col.map((g) => (
                <DebugCard key={g.id} group={g} relatedOf={relatedOf} onChoose={choose} />
              ))}
            </div>
          ))}
        </div>
      </div>
      {port && relations ? (
        <SelectedPort
          port={port}
          upstream={relations.upstream}
          downstream={relations.downstream}
          ports={ports}
          everything={everything}
          onEverything={() => setEverything((v) => !v)}
          onClose={clear}
        />
      ) : null}
    </section>
  );
}

export default DebugView;
