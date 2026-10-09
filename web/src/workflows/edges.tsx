/**
 * The Detailed view's bundled edge: one line between two compact cards standing
 * for every wire between them, labelled with the wire count when there is more
 * than one ("3", read out as "3 connections"). Dashed and lit come from the
 * edge's own class, as on a port-to-port wire (../Canvas.tsx); the label, which
 * React Flow renders outside the edge's SVG group, carries them as its own classes.
 */
import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Edge, type EdgeProps } from "@xyflow/react";

export interface BundleEdgeData extends Record<string, unknown> {
  count: number;
  cross: boolean;
  lit: boolean;
}

export type BundleEdgeType = Edge<BundleEdgeData, "bundle">;

export const connectionsLabel = (count: number) => `${count} connection${count === 1 ? "" : "s"}`;

export function BundleEdge(props: EdgeProps<BundleEdgeType>) {
  const { id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, markerEnd, style } = props;
  const [path, labelX, labelY] = getBezierPath({ sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition });
  const count = data?.count ?? 1;
  return (
    <>
      <BaseEdge id={id} path={path} markerEnd={markerEnd} style={style} />
      {count > 1 ? (
        <EdgeLabelRenderer>
          <div
            className={`wf-edge-count${data?.cross ? " wf-edge-count--cross" : ""}${data?.lit ? " is-lit" : ""}`}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            role="img"
            aria-label={connectionsLabel(count)}
            data-edge={id}
          >
            {count}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const EDGE_TYPES = { bundle: BundleEdge };
