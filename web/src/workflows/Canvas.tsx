/**
 * The workflow canvas: an @xyflow/react node editor over the pure model.
 * Inputs → steps → Outputs, laid out by elkjs, typed handles that refuse a
 * mismatched wire, dashed edges for machine hops and — with a run overlaid —
 * the travelled path lit and each step badged with its host and outcome.
 *
 * Zoom stays at 1 (the board's 190px cards); the canvas pans, it does not
 * scroll-zoom, so the page keeps scrolling under the wheel.
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
import { useCallback, useEffect, useMemo, useState } from "react";
import type { Machine } from "../api/types";
import type { Actor, Step, WorkflowDef } from "../api/workflows";
import { machineColors } from "../culture-design/chart";
import { CARD_WIDTH, columnLayout, layoutWorkflow, type Positions } from "./layout";
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
  onConnect: (c: Connection) => void;
  onRefused: (reason: string) => void;
}

const TOP = 80; // room for the selected step's toolbar above the top row
const MIN_HEIGHT = 570; // the board's canvas: 530 + 2 × 20 padding

function subtitleOf(step: Step): string | null {
  const config = step.config ?? {};
  const bits = [config.harness, config.model].filter((v): v is string => typeof v === "string" && v !== "");
  if (bits.length) return bits.join(", ");
  if (step.placement?.actor) return step.placement.actor;
  return null;
}

function CanvasInner(props: CanvasProps) {
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
    layoutWorkflow(workflow).then((p) => {
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
      selectable: false,
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

  const bounds = useMemo(() => {
    const xs = Object.values(positions).map((p) => p.x);
    const ys = Object.values(positions).map((p) => p.y);
    const minX = xs.length ? Math.min(...xs) : 0;
    const maxX = xs.length ? Math.max(...xs) + CARD_WIDTH : CARD_WIDTH;
    const minY = ys.length ? Math.min(...ys) : 0;
    const maxY = ys.length ? Math.max(...ys) + 200 : 200;
    return { minX, maxX, minY, maxY };
  }, [positions]);
  const height = Math.max(MIN_HEIGHT, bounds.maxY - bounds.minY + TOP + 110);

  // Zoom 1, centred horizontally when it fits, toolbar room on top.
  const [width, setWidth] = useState(0);
  const containerRef = useCallback((el: HTMLDivElement | null) => {
    if (el) setWidth(el.clientWidth);
  }, []);
  useEffect(() => {
    const graph = bounds.maxX - bounds.minX;
    const x = width > graph ? (width - graph) / 2 - bounds.minX : 20 - bounds.minX;
    flow.setViewport({ x, y: TOP - bounds.minY, zoom: 1 });
  }, [bounds, width, flow]);

  const edges = useMemo<Edge[]>(() => {
    const graph = graphEdges(workflow, ctx, overlay);
    const lit = litEdges(graph, overlay);
    const name = (node: string) =>
      node === INPUTS_NODE
        ? "Inputs"
        : node === OUTPUTS_NODE
          ? "Outputs"
          : stepLabel((workflow.steps ?? []).find((s) => s.id === node) ?? { id: node, kind: "logic" });
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

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      onNodesChangeBase(changes.filter((c) => c.type !== "remove"));
      for (const c of changes) {
        if (c.type !== "select") continue;
        if (c.selected && c.id !== INPUTS_NODE && c.id !== OUTPUTS_NODE) props.onSelect(c.id);
        else if (!c.selected && c.id === selected) props.onSelect(null);
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [onNodesChangeBase, selected, props.onSelect],
  );

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
    <section className="wf-canvas" aria-label="Workflow canvas" style={{ height }} ref={containerRef}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodesChange={onNodesChange}
        onNodeClick={(_, node) => {
          if (node.id !== INPUTS_NODE && node.id !== OUTPUTS_NODE) props.onSelect(node.id);
        }}
        onPaneClick={() => props.onSelect(null)}
        onConnect={(c) => {
          const conn = asConnection(c);
          if (conn) props.onConnect(conn);
        }}
        onConnectEnd={onConnectEnd}
        isValidConnection={isValidConnection}
        deleteKeyCode={null}
        zoomOnScroll={false}
        zoomOnPinch={false}
        zoomOnDoubleClick={false}
        preventScrolling={false}
        minZoom={1}
        maxZoom={1}
        defaultViewport={{ x: 20, y: TOP, zoom: 1 }}
        nodeOrigin={[0, 0]}
        edgesFocusable={false}
        connectionRadius={24}
      />
      <button type="button" className="wf-add-step" aria-label="Add step" onClick={props.onAddStep}>
        +
      </button>
    </section>
  );
}

export function WorkflowCanvas(props: CanvasProps) {
  return (
    <ReactFlowProvider>
      <CanvasInner {...props} />
    </ReactFlowProvider>
  );
}

export default WorkflowCanvas;
