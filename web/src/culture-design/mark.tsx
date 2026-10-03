// culture-design/mark.tsx
//
// Copied from culture-nodes web/src/culture-design/mark.tsx (same org pin as
// ./tokens.css); "the ADR" below is culture-nodes' docs/adr/0001.
//
// Ported from agentculture/org's Mark.astro — pinned commit
// b4d939ba0aa354a5ae53065319a773e0013de698,
// site-astro/src/components/Mark.astro:
//
//   The organization's mark: three agents, two threads — the smallest
//   mesh that is still a culture. Echoes the hero constellation and the
//   favicon.
//
// Geometry (viewBox, path, circle radii/positions) is copied verbatim
// from the Astro source. Colors ride the same CSS custom properties as
// the rest of this layer (--mesh-node / --mesh-thread, defined in
// ./tokens.css) so the mark stays theme-aware with zero JS: light/dark
// follow prefers-color-scheme exactly as tokens.css defines it — there is
// no toggle upstream (see docs/adr/0001-culture-design-source.md).
//
// The placeholder `type FC<P> = (props: P) => any` this file carried under
// task t5 is gone: @types/react is on the dependency tree as of the web
// build-out, so the component is typed against React's own `FC` and
// type-checked by `tsc -b`. Geometry (viewBox, path, circle radii and
// positions) is unchanged from the Astro source.

import type { FC } from "react";

export interface MarkProps {
  /** Rendered width/height in px. Astro source default: 26. */
  size?: number;
  /**
   * Accessible name. The SVG itself is always `aria-hidden="true"`, as in
   * the Astro source (the mark is decorative next to sited text). Pass a
   * title for standalone/logo use where the mark needs an accessible name:
   * it renders as visually hidden text beside the SVG, so the surrounding
   * link or element is named by it and no `role="img"` is needed.
   */
  title?: string;
}

export const Mark: FC<Readonly<MarkProps>> = ({ size = 26, title }: Readonly<MarkProps>) => (
  <>
    <svg className="mark" width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <path
        d="M14 46 Q 24 38 33 21 M33 21 Q 40 36 51 41"
        stroke="var(--mesh-thread)"
        strokeWidth={2.5}
        strokeLinecap="round"
      />
      <circle cx={14} cy={46} r={5} fill="var(--mesh-node)" />
      <circle cx={33} cy={21} r={6.5} fill="var(--mesh-node)" />
      <circle cx={51} cy={41} r={4.5} fill="var(--mesh-node)" />
    </svg>
    {title ? <span className="sr-only">{title}</span> : null}
  </>
);

export default Mark;
