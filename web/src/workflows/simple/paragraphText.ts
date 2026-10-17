/**
 * What the Simple view shows of the API's paragraph (spec c13, h10).
 *
 * The editor builds no phrases (o4): every joining phrase and every rule or Then
 * sentence arrives in the segments from GET /workflows/{id}/describe, composed
 * by the API from its one table (culture_rules/model/paragraph.py). The only things
 * done here are the two grouping edits the API's own `paragraph.text` applies to a
 * segment marked `grouped` (the previous description's final period is dropped,
 * the grouped description's first letter is lower-cased) and the visible stand-in
 * for a step with no description, so the segments read as `paragraph.text` does.
 */
import type { ParagraphSegment } from "../../api/types";

/** One step segment as it reads on screen. */
export interface StepView {
  seg: ParagraphSegment;
  /** The description as shown (a gap shows its stand-in; a fallback its generated line). */
  shown: string;
  /** The step's own description (what the inline editor starts from); empty for a gap. */
  description: string;
  gap: boolean;
  fallback: boolean;
  /** Joined to the previous step's sentence: no space before its phrase. */
  joined: boolean;
}

const str = (v: unknown) => (typeof v === "string" ? v : "");

/** `Packs it.` -> `packs it.`; not before a non-letter or an all-caps word such as `EPUB`. */
export function lowerFirst(text: string): string {
  const word = text.split(" ", 1)[0];
  const first = text.charAt(0);
  const upper = first !== first.toLowerCase() && first === first.toUpperCase();
  const shouting = word.length > 1 && word === word.toUpperCase() && word !== word.toLowerCase();
  return !upper || shouting ? text : first.toLowerCase() + text.slice(1);
}

/** The visible stand-in for a step with no description: it names the step. */
export const gapText = (name: string) => `[${name} has no description yet]`;

/** The step segments (in order) as they read, with the grouping edits applied. */
export function stepViews(segments: readonly ParagraphSegment[]): StepView[] {
  const views: StepView[] = segments.map((seg) => {
    const fallback = seg.fallback === true;
    const description = fallback ? "" : str(seg.text);
    const gap = !fallback && description === "";
    return {
      seg,
      shown: gap ? gapText(str(seg.name) || str(seg.step)) : str(seg.text),
      description,
      gap,
      fallback,
      joined: false,
    };
  });
  views.forEach((view, i) => {
    const prev = views[i - 1];
    if (view.seg.grouped !== true || !prev || !prev.shown.endsWith(".")) return;
    prev.shown = prev.shown.slice(0, -1);
    view.shown = lowerFirst(view.shown);
    view.joined = true;
  });
  return views;
}
