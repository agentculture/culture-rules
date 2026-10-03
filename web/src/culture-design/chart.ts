// culture-design/chart.ts
//
// The categorical chart palette — and, because a machine keeps one color
// everywhere it appears (rule list dots, placement chips, Statistics rows),
// the machine palette too.
//
// light: the design canvas's machine colors ('Chosen — Statistics' board,
//        https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi): spark teal,
//        thor amber, spark2 blue.
// dark:  the same three hues lifted into the dark lightness band.
//
// Both sets pass the dataviz validator (scripts/validate_palette.js) on
// tokens.css's surfaces — run `npm run check:palette`. Changing a hex here
// means re-running it; the check parses the arrays below literally.

export const CHART_PALETTE = {
  light: ["#0a8a78", "#b4531f", "#3b4fb0"],
  dark: ["#2fa58f", "#d0743d", "#6a7fe0"],
} as const;

/** Offline / unplaced machines (the canvas's orin row). */
export const NEUTRAL_MACHINE = "#8a8fa8";

/**
 * Machine name -> palette slot, in the order the API lists machines. Slots
 * cycle past the end of the palette; with more machines than slots, the
 * name is always written next to the color (never color alone).
 */
export function machineColors(names: readonly string[]): Map<string, number> {
  const slots = new Map<string, number>();
  names.forEach((name, i) => slots.set(name, i % CHART_PALETTE.light.length));
  return slots;
}
