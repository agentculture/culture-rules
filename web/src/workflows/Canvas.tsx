/**
 * The workflow canvas: an @xyflow/react node editor over the pure model.
 * Inputs → steps → Outputs, laid out by elkjs, typed handles that refuse a
 * mismatched wire, dashed edges for machine hops and — with a run overlaid —
 * the travelled path lit and each step badged with its host and outcome.
 *
 * Zoom 1 is the board's 190px cards. A plain wheel never zooms: the page keeps
 * scrolling under it (why zoom was locked before d19). Zoom is a pinch, ctrl/cmd
 * + wheel, the on-canvas zoom out / zoom in / fit buttons, or `+` / `-` / `0`
 * while focus is in the canvas, within 25%–200% (./zoom.ts). The canvas is sized
 * like a document at its zoom, so zooming out shrinks it to fit a wide graph.
 */
import {
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  useUpdateNodeInternals,
  type Connection as FlowConnection,
  type Edge,
  type FinalConnectionState,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Machine } from "../api/types";
import type { Actor, Step, WorkflowDef } from "../api/workflows";
import { machineColors } from "../culture-design/chart";
import {
  CANVAS_TOP,
  canvasBounds,
  canvasHeight,
  columnLayout,
  layoutWorkflow,
  nodeHeights,
  type Positions,
} from "./layout";
import {
  INPUTS_NODE,
  OUTPUTS_NODE,
  connectionProblem,
  graphEdges,
  litEdges,
  placementLabel,
  stepLabel,
  stepMachine,
  type Connection,
  type RunOverlay,
} from "./model";
import { NODE_TYPES, type IoNodeData, type StepNodeData } from "./nodes";
import {
  MAX_ZOOM,
  MIN_ZOOM,
  ZOOM_DURATION_MS,
  clampZoom,
  fitZoom,
  prefersReducedMotion,
  stepZoom,
  zoomKey,
  zoomPercent,
} from "./zoom";

export interface CanvasProps {
  workflow: WorkflowDef;
  machines: readonly Machine[];
  actors: readonly Actor[];
  overlay: RunOverlay | null;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onToggle: (id: string) => void;
  onPlacement: (id: string, trigger: HTMLElement) => void;
  onEdit: (id: string, trigger: HTMLElement) => void;
  onDelete: (id: string) => void;
  onAddStep: () => void;
  /** The `in` / `out` node was chosen (click, or Enter / Space while focused): open its editor. */
  onOpenIo: (id: typeof INPUTS_NODE | typeof OUTPUTS_NODE, trigger: HTMLElement | null) => void;
  onConnect: (c: Connection) => void;
  onRefused: (reason: string) => void;
}

const TOP = CANVAS_TOP; // room for the selected step's toolbar above the top row
const EDGE_ROOM = 20; // the board's canvas padding, left and right of a graph that does not fit

const isIo = (id: string): id is typeof INPUTS_NODE | typeof OUTPUTS_NODE =>
  id === INPUTS_NODE || id === OUTPUTS_NODE;

/** An edge end's name, for the wire's accessible label. */
function nodeName(workflow: WorkflowDef, node: string): string {
  if (node === INPUTS_NODE) return "Inputs";
  if (node === OUTPUTS_NODE) return "Outputs";
  return stepLabel((workflow.steps ?? []).find((s) => s.id === node) ?? { id: node, kind: "logic" });
}

/** The heights React Flow has measured so far, by node id. */
function measuredHeights(nodes: readonly Node[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const n of nodes) if (n.measured?.height) out[n.id] = n.measured.height;
  return out;
}

function subtitleOf(step: Step): string | null {
  const config = step.config ?? {};
  const bits = [config.harness, config.model].filter((v): v is string => typeof v === "string" && v !== "");
  if (bits.length) return bits.join(", ");
  if (step.placement?.actor) return step.placement.actor;
  return null;
}

/** Is a keydown aimed at a text field (whose `+` / `-` / `0` are typing, not zoom)? */
function isTyping(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/** The on-canvas zoom buttons: large targets, labelled, with the current zoom read out. */
function ZoomControls({
  zoom,
  onZoom,
}: Readonly<{ zoom: number; onZoom: (to: "in" | "out" | "fit") => void }>) {
  return (
    <div className="wf-zoom" role="group" aria-label="Zoom">
      <button
        type="button"
        className="wf-zoom__button"
        aria-label="Zoom out"
        aria-keyshortcuts="-"
        disabled={zoom <= MIN_ZOOM}
        onClick={() => onZoom("out")}
      >
        <span aria-hidden="true">−</span>
      </button>
      {/* not an <output>: that is a second "status" beside the board's own */}
      <span className="wf-zoom__level" aria-live="polite" data-testid="zoom-level">
        <span className="sr-only">Zoom </span>
        {zoomPercent(zoom)}
      </span>
      <button
        type="button"
        className="wf-zoom__button"
        aria-label="Zoom in"
        aria-keyshortcuts="+"
        disabled={zoom >= MAX_ZOOM}
        onClick={() => onZoom("in")}
      >
        <span aria-hidden="true">+</span>
      </button>
      <button
        type="button"
        className="wf-zoom__button wf-zoom__button--fit"
        aria-label="Fit to width"
        aria-keyshortcuts="0"
        onClick={() => onZoom("fit")}
      >
        Fit
      </button>
    </div>
  );
}

function CanvasInner(props: Readonly<CanvasProps>) {
  const { workflow, machines, actors, overlay, selected } = props;
  const flow = useReactFlow();
  const slots = useMemo(() => machineColors(machines.map((m) => m.name)), [machines]);
  const ctx = useMemo(() => ({ machines, actors }), [machines, actors]);

  // Layout on load and whenever the set of steps changes; drags are kept otherwise.
  const shape = `${workflow.id}|${(workflow.steps ?? []).map((s) => s.id).join(",")}`;
  const [positions, setPositions] = useState<Positions>(() => columnLayout(workflow));
  useEffect(() => {
    let live = true;
    setPositions(columnLayout(workflow));
    // `void` is honest here: layoutWorkflow never rejects (an ELK failure resolves to the
    // column layout already shown), and the callback only sets state.
    void layoutWorkflow(workflow).then((p) => {
      if (live) setPositions(p);
    });
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape]);

  const built = useMemo<Node[]>(() => {
    const stepNodes: Node<StepNodeData>[] = (workflow.steps ?? []).map((step) => {
      const where = stepMachine(step, ctx);
      const slot = where.machine !== null && slots.has(where.machine) ? slots.get(where.machine)! : null;
      const run = overlay?.get(step.id) ?? null;
      const label = stepLabel(step);
      return {
        id: step.id,
        type: "step",
        position: { x: 0, y: 0 },
        ariaLabel: label,
        domAttributes: {
          "data-machine-slot": slot === null ? "none" : String(slot),
          ...(run ? { "data-run-status": run.status } : {}),
        } as Node["domAttributes"],
        data: {
          step,
          label,
          slot,
          host: where.machine ?? placementLabel(step.placement),
          subtitle: subtitleOf(step),
          placement: placementLabel(step.placement),
          run,
          onToggle: props.onToggle,
          onPlacement: props.onPlacement,
          onEdit: props.onEdit,
          onDelete: props.onDelete,
        },
      };
    });
    const io = (side: "in" | "out"): Node<IoNodeData> => ({
      id: side === "in" ? INPUTS_NODE : OUTPUTS_NODE,
      type: "io",
      position: { x: 0, y: 0 },
      ariaLabel: side === "in" ? "Inputs" : "Outputs",
      data: {
        side,
        ports:
          side === "in"
            ? (workflow.inputs ?? [])
            : (workflow.outputs ?? []).map((o) => ({ name: o.name, type: o.type })),
      },
    });
    return [io("in"), ...stepNodes, io("out")];
    // Callbacks are stable (useCallback upstream).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workflow, ctx, slots, overlay]);

  const [nodes, setNodes, onNodesChangeBase] = useNodesState<Node>([]);

  // Data / selection changes keep each node's place (and its measurement).
  useEffect(() => {
    setNodes((prev) =>
      built.map((n) => {
        const before = prev.find((p) => p.id === n.id);
        return {
          ...n,
          position: before?.position ?? positions[n.id] ?? { x: 0, y: 0 },
          measured: before?.measured,
          selected: n.id === selected,
        };
      }),
    );
  }, [built, selected, setNodes]); // eslint-disable-line react-hooks/exhaustive-deps

  // A new layout moves every node.
  useEffect(() => {
    setNodes((prev) => prev.map((n) => ({ ...n, position: positions[n.id] ?? n.position })));
  }, [positions, setNodes]);

  // Replacing a node object before React Flow has handed back its measurement
  // drops the node's handle bounds (and with them its edges) until the next
  // resize, which never comes. Re-measure after every data or layout update.
  const updateNodeInternals = useUpdateNodeInternals();
  useEffect(() => {
    const ids = built.map((n) => n.id);
    const frame = requestAnimationFrame(() => updateNodeInternals(ids));
    return () => cancelAnimationFrame(frame);
  }, [built, positions, selected, updateNodeInternals]);

  // Each card's real height: its port count, or what React Flow measured if taller. Keyed
  // as a string so a selection (a new nodes array, same sizes) never re-centres the canvas.
  const heightKey = JSON.stringify(nodeHeights(workflow, measuredHeights(nodes)));
  const bounds = useMemo(
    () => canvasBounds(positions, JSON.parse(heightKey) as Record<string, number>),
    [positions, heightKey],
  );
  const [zoom, setZoom] = useState(1);
  // Set by a button or key zoom: the next viewport move animates (unless reduced motion).
  const animateNext = useRef(false);
  const height = canvasHeight(bounds, zoom);
  const graphWidth = (bounds.maxX - bounds.minX) * zoom;

  // At the zoom, centred horizontally when it fits, toolbar room on top. A graph wider
  // than the canvas (beside the workflow list) scrolls sideways, as the design
  // board's canvas does (`overflow-x: auto`), instead of being clipped.
  // The canvas's width, kept current: read when the section mounts, then from a
  // ResizeObserver, so a narrowed window or a turned phone re-centres and re-fits.
  const [width, setWidth] = useState(0);
  // One stable ref for the section: an inline ref is a new function every render, so React
  // re-runs it (null, then the element) on every commit, and setWidth inside it can loop.
  const sectionCallbackRef = useCallback((el: HTMLElement | null) => {
    sectionRef.current = el;
    if (el) setWidth(el.clientWidth);
  }, []);
  useEffect(() => {
    const section = sectionRef.current;
    if (!section || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => setWidth(section.clientWidth));
    observer.observe(section);
    return () => observer.disconnect();
  }, []);
  const flowWidth = Math.max(width, graphWidth + 2 * EDGE_ROOM);
  useEffect(() => {
    const graph = (bounds.maxX - bounds.minX) * zoom;
    const left = bounds.minX * zoom;
    const x = width > graph ? (width - graph) / 2 - left : EDGE_ROOM - left;
    const duration = animateNext.current && !prefersReducedMotion() ? ZOOM_DURATION_MS : 0;
    animateNext.current = false;
    // React Flow resolves this once the transform is applied. It only rejects if d3 throws
    // while applying it; the viewport is cosmetic, so say so in the console and carry on.
    flow.setViewport({ x, y: TOP - bounds.minY * zoom, zoom }, { duration }).catch((err: unknown) => {
      console.warn("could not position the workflow canvas", err);
    });
  }, [bounds, width, zoom, flow]);

  /** A button or key zoom: one step in or out, or fit the graph to the canvas width. */
  const zoomTo = useCallback(
    (to: "in" | "out" | "fit") => {
      animateNext.current = true;
      setZoom((z) => {
        // the width now, not the last one rendered: a resize may not have reached state yet
        const now = sectionRef.current?.clientWidth || width;
        if (to === "fit") return fitZoom(bounds.maxX - bounds.minX, now, EDGE_ROOM);
        return stepZoom(z, to === "in" ? 1 : -1);
      });
    },
    [bounds, width],
  );
  const zoomToRef = useRef(zoomTo);
  useEffect(() => {
    zoomToRef.current = zoomTo;
  }, [zoomTo]);

  const edges = useMemo<Edge[]>(() => {
    const graph = graphEdges(workflow, ctx, overlay);
    const lit = litEdges(graph, overlay);
    const name = (node: string) => nodeName(workflow, node);
    return graph.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourcePort,
      targetHandle: e.targetPort,
      selectable: false,
      focusable: false,
      className: `wf-edge ${e.cross ? "wf-edge--cross" : "wf-edge--same"}${lit.has(e.id) ? " is-lit" : ""}`,
      ariaLabel: `${name(e.source)} ${e.sourcePort} to ${name(e.target)} ${e.targetPort}, ${
        e.cross ? "crosses machines" : "same machine"
      }`,
    }));
  }, [workflow, ctx, overlay]);

  const sectionRef = useRef<HTMLElement | null>(null);
  const nodeElement = (id: string) =>
    sectionRef.current?.querySelector<HTMLElement>(`.react-flow__node[data-id="${CSS.escape(id)}"]`) ?? null;
  /** A node chosen by click or keyboard: a step is selected, `in` / `out` open their editor. */
  const choose = (id: string) => {
    if (isIo(id)) props.onOpenIo(id, nodeElement(id));
    else props.onSelect(id);
  };

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      onNodesChangeBase(changes.filter((c) => c.type !== "remove"));
      for (const c of changes) {
        if (c.type !== "select") continue;
        if (c.selected) choose(c.id);
        else if (c.id === selected) props.onSelect(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [onNodesChangeBase, selected, props.onSelect, props.onOpenIo],
  );

  // Enter / Space on a focused in / out node always opens its editor, even while the node is
  // still selected (React Flow only reports a selection *change*). The listener is native, on
  // the section, because the nodes are React Flow's own focusable elements: the section has
  // no interactive role of its own to hang a React handler on.
  const openIoRef = useRef(props.onOpenIo);
  useEffect(() => {
    openIoRef.current = props.onOpenIo;
  }, [props.onOpenIo]);
  useEffect(() => {
    const section = sectionRef.current;
    if (!section) return;
    const onKeyDown = (e: KeyboardEvent) => {
      const zoomTo = isTyping(e.target) ? null : zoomKey(e);
      if (zoomTo) {
        e.preventDefault();
        zoomToRef.current(zoomTo);
        return;
      }
      if (e.key !== "Enter" && e.key !== " ") return;
      const target = e.target as HTMLElement;
      const id = target.classList.contains("react-flow__node") ? target.dataset.id : undefined;
      if (!id || !isIo(id)) return;
      e.preventDefault();
      openIoRef.current(id, target);
    };
    // cmd + wheel zooms as ctrl + wheel does (React Flow's pinch path handles ctrl); a plain
    // wheel is left alone, so the page scrolls.
    const onWheel = (e: WheelEvent) => {
      if (!e.metaKey || e.ctrlKey || e.deltaY === 0) return;
      e.preventDefault();
      zoomToRef.current(e.deltaY < 0 ? "in" : "out");
    };
    section.addEventListener("keydown", onKeyDown);
    section.addEventListener("wheel", onWheel, { passive: false });
    return () => {
      section.removeEventListener("keydown", onKeyDown);
      section.removeEventListener("wheel", onWheel);
    };
  }, []);

  const asConnection = (c: FlowConnection | Edge): Connection | null =>
    c.source && c.target && c.sourceHandle && c.targetHandle
      ? { source: c.source, sourcePort: c.sourceHandle, target: c.target, targetPort: c.targetHandle }
      : null;

  const isValidConnection = useCallback(
    (c: FlowConnection | Edge) => {
      const conn = asConnection(c);
      return conn !== null && connectionProblem(workflow, conn) === null;
    },
    [workflow],
  );

  const onConnectEnd = useCallback(
    (_: unknown, state: FinalConnectionState) => {
      if (state.isValid || !state.fromHandle || !state.toHandle) return;
      const from = state.fromHandle;
      const to = state.toHandle;
      const forward = from.type === "source";
      const conn: Connection = forward
        ? { source: from.nodeId, sourcePort: from.id ?? "", target: to.nodeId, targetPort: to.id ?? "" }
        : { source: to.nodeId, sourcePort: to.id ?? "", target: from.nodeId, targetPort: from.id ?? "" };
      const problem = connectionProblem(workflow, conn);
      if (problem) props.onRefused(problem);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [workflow, props.onRefused],
  );

  return (
    <section
      className="wf-canvas"
      aria-label="Workflow canvas"
      style={{ height }}
      ref={sectionCallbackRef}
    >
      <div className="wf-canvas__scroll">
        {/* React Flow pins its own wrapper to 100%: the width goes on a box around it. */}
        <div className="wf-canvas__graph" style={{ width: flowWidth }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            onNodesChange={onNodesChange}
            onNodeClick={(_, node) => choose(node.id)}
            onPaneClick={() => props.onSelect(null)}
            onConnect={(c) => {
              const conn = asConnection(c);
              if (conn) props.onConnect(conn);
            }}
            onConnectEnd={onConnectEnd}
            isValidConnection={isValidConnection}
            deleteKeyCode={null}
            zoomOnScroll={false}
            zoomOnPinch
            zoomOnDoubleClick={false}
            zoomActivationKeyCode={null}
            preventScrolling={false}
            minZoom={MIN_ZOOM}
            maxZoom={MAX_ZOOM}
            onMove={(event, viewport) => {
              // a pinch or ctrl + wheel (a user event); our own setViewport passes none
              if (event) setZoom(clampZoom(viewport.zoom));
            }}
            defaultViewport={{ x: 20, y: TOP, zoom: 1 }}
            nodeOrigin={[0, 0]}
            edgesFocusable={false}
            connectionRadius={24}
          />
        </div>
      </div>
      {(workflow.steps ?? []).length === 0 ? (
        <section className="wf-start" aria-label="Get started">
          <p className="wf-start__lead">Start with a step, or with the inputs it will read.</p>
          <div className="wf-start__actions">
            <button type="button" className="wf-button wf-button--primary wf-button--large" onClick={props.onAddStep}>
              <span aria-hidden="true">+</span> Add a step
            </button>
            <button
              type="button"
              className="wf-button wf-button--large"
              aria-haspopup="dialog"
              onClick={(e) => props.onOpenIo(INPUTS_NODE, e.currentTarget)}
            >
              Add an input
            </button>
          </div>
        </section>
      ) : null}
      <ZoomControls zoom={zoom} onZoom={zoomTo} />
      <button type="button" className="wf-add-step" aria-label="Add step" onClick={props.onAddStep}>
        +
      </button>
    </section>
  );
}

export function WorkflowCanvas(props: Readonly<CanvasProps>) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

export default WorkflowCanvas;
