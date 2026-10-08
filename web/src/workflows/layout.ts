/**
 * Left-to-right layered layout of a workflow graph with elkjs: Inputs in the
 * first layer, Outputs in the last, each step one layer after its latest
 * upstream — the 'Chosen — Workflows' board's columns (190px cards, 60px
 * between layers). Positions are top-left corners in flow coordinates.
 *
 * elkjs is ~1.4 MB, so it is loaded on first use (its own chunk); until it
 * answers, and if it ever fails, a deterministic column layout stands in.
 */
import type { ELK as ElkApi } from "elkjs/lib/elk-api";
import type { WorkflowDef } from "../api/workflows";
import { INPUTS_NODE, OUTPUTS_NODE, parseOutputSource } from "./model";

export const CARD_WIDTH = 190;
const HEADER = 40;
const TITLE = 28;
const PORT_ROW = 30;
const PORT_PAD = 18;
/** Room for a run overlay's "succeeded on spark" line, so a run never makes cards touch. */
const RUN_ROW = 30;

export type Positions = Record<string, { x: number; y: number }>;

/** A card's height on the board: header, title, one 30px row per port. */
export function cardHeight(ports: number, io = false): number {
  return io ? HEADER + 24 + PORT_ROW * ports : HEADER + TITLE + PORT_PAD + PORT_ROW * ports + RUN_ROW;
}

/** Each node's height on the board, from its port count — or its measured height, if taller. */
export function nodeHeights(wf: WorkflowDef, measured: Readonly<Record<string, number>> = {}): Record<string, number> {
  const out: Record<string, number> = {};
  for (const n of graphOf(wf).nodes) out[n.id] = Math.max(n.height, measured[n.id] ?? 0);
  return out;
}

/** Room above the top row for the selected step's toolbar. */
export const CANVAS_TOP = 80;
/** The board's canvas: 530 + 2 × 20 padding. */
export const CANVAS_MIN_HEIGHT = 570;
/** Room under the lowest card for the step `+` (48px, 24px off the bottom) and air. */
const CANVAS_BOTTOM = 110;

export interface Bounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** The graph's extent in flow coordinates: every card's real width and height. */
export function canvasBounds(positions: Positions, heights: Readonly<Record<string, number>>): Bounds {
  const ids = Object.keys(positions);
  if (ids.length === 0) return { minX: 0, maxX: CARD_WIDTH, minY: 0, maxY: 0 };
  const xs = ids.map((id) => positions[id].x);
  const ys = ids.map((id) => positions[id].y);
  const bottoms = ids.map((id) => positions[id].y + (heights[id] ?? cardHeight(0)));
  return {
    minX: Math.min(...xs),
    maxX: Math.max(...xs) + CARD_WIDTH,
    minY: Math.min(...ys),
    maxY: Math.max(...bottoms),
  };
}

/**
 * The canvas's pixel height: toolbar room, the graph (scaled by `zoom`), room for the `+`;
 * never under the board's.
 */
export function canvasHeight(bounds: Bounds, zoom = 1): number {
  return Math.max(CANVAS_MIN_HEIGHT, CANVAS_TOP + (bounds.maxY - bounds.minY) * zoom + CANVAS_BOTTOM);
}

function graphOf(wf: WorkflowDef) {
  const steps = wf.steps ?? [];
  const nodes = [
    { id: INPUTS_NODE, width: CARD_WIDTH, height: cardHeight((wf.inputs ?? []).length, true) },
    ...steps.map((s) => ({
      id: s.id,
      width: CARD_WIDTH,
      height: cardHeight((s.inputs ?? []).length + (s.outputs ?? []).length),
    })),
    { id: OUTPUTS_NODE, width: CARD_WIDTH, height: cardHeight((wf.outputs ?? []).length, true) },
  ];
  const ids = new Set(nodes.map((n) => n.id));
  const pairs = new Set<string>();
  for (const e of wf.edges ?? []) pairs.add(`${e.source}\u0000${e.target}`);
  for (const o of wf.outputs ?? []) {
    const from = parseOutputSource(o.source);
    if (from) pairs.add(`${from.node}\u0000${OUTPUTS_NODE}`);
  }
  const edges = [...pairs]
    .map((p) => p.split("\u0000"))
    .filter(([s, t]) => ids.has(s) && ids.has(t) && s !== t)
    .map(([source, target], i) => ({ id: `e${i}`, sources: [source], targets: [target] }));
  return { nodes, edges };
}

/** Deterministic fallback (no elk): longest-path layers, stacked in order. */
export function columnLayout(wf: WorkflowDef): Positions {
  const { nodes, edges } = graphOf(wf);
  const layer = new Map<string, number>(nodes.map((n) => [n.id, 0]));
  // One relaxation pass per node bounds the longest path.
  for (const _pass of nodes) {
    for (const e of edges) {
      const next = (layer.get(e.sources[0]) ?? 0) + 1;
      if (next > (layer.get(e.targets[0]) ?? 0)) layer.set(e.targets[0], next);
    }
  }
  const last = Math.max(1, ...[...layer.entries()].filter(([id]) => id !== OUTPUTS_NODE).map(([, l]) => l + 1));
  layer.set(OUTPUTS_NODE, Math.max(layer.get(OUTPUTS_NODE) ?? 0, last));
  const heights = new Map<number, number>();
  const out: Positions = {};
  for (const n of nodes) {
    const l = layer.get(n.id) ?? 0;
    const y = heights.get(l) ?? 0;
    out[n.id] = { x: l * (CARD_WIDTH + 60), y };
    heights.set(l, y + n.height + 40);
  }
  return out;
}

let elk: ElkApi | null = null;

export async function elkLayout(wf: WorkflowDef): Promise<Positions> {
  const { nodes, edges } = graphOf(wf);
  if (!elk) {
    const { default: ELK } = await import("elkjs/lib/elk.bundled.js");
    elk = new ELK();
  }
  const result = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.layered.spacing.nodeNodeBetweenLayers": "60",
      "elk.spacing.nodeNode": "50",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      // Each step as early as its inputs allow (the board's Run tests sits beside Fetch diff).
      "elk.layered.layering.strategy": "LONGEST_PATH_SOURCE",
      "elk.layered.crossingMinimization.semiInteractive": "true",
    },
    children: nodes.map((n, i) => ({
      ...n,
      layoutOptions: {
        "elk.position": `(0,${i})`,
        ...(n.id === INPUTS_NODE ? { "elk.layered.layering.layerConstraint": "FIRST" } : {}),
        ...(n.id === OUTPUTS_NODE ? { "elk.layered.layering.layerConstraint": "LAST" } : {}),
      },
    })),
    edges,
  });
  const out: Positions = {};
  for (const child of result.children ?? []) out[child.id] = { x: child.x ?? 0, y: child.y ?? 0 };
  return out;
}

/** elk when it can, the column fallback when it cannot. */
export async function layoutWorkflow(wf: WorkflowDef): Promise<Positions> {
  try {
    return await elkLayout(wf);
  } catch {
    return columnLayout(wf);
  }
}
