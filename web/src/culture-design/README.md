# culture-design

The culture-rules editor's design layer: the AgentCulture visual system,
pinned to the org repo exactly as culture-nodes pins it (culture-nodes
`web/src/culture-design/`, its ADR 0001), plus the rule-flow vocabulary of
the design canvas's 'Chosen' row
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>).

## Contents

- `tokens.css` — agentculture/org `site-astro/src/styles/global.css`,
  copied **verbatim** below a provenance header that records the pinned
  commit and the source's sha256. Do not hand-edit the copied section.
  Imported once, globally, from `src/main.tsx`.
- `mark.tsx` — the AgentCulture mark, copied from culture-nodes' port of
  org's `Mark.astro` (same pin).
- `chart.ts` — the categorical chart palette, which is also the machine
  palette: light `#0a8a78` / `#b4531f` / `#3b4fb0` (the canvas's machine
  colors) and a dark set lifted into the dark lightness band.
- `stages.tsx` — the four stage shapes (Trigger slab, dashed Condition,
  bordered Workflow with offset shadow, Action pill), the relationship
  card, the `+` add-stage button, machine dots and the switch. Their CSS is
  `src/styles/stages.css`.

## Verification

```bash
npm run check:tokens    # tokens.css byte-identity (scripts/check-tokens.mjs)
npm run check:palette   # chart palette through scripts/validate_palette.js
```

`check-tokens.mjs` always checks the copied body against the recorded
sha256; when an org checkout is reachable (`CULTURE_DESIGN_ORG_REPO`,
default `/home/spark/git/org`) it also reads the source at the pin with
`git show <pin>:<path>` and asserts byte equality.

## Re-pin

1. `git -C /home/spark/git/org rev-parse HEAD` for the new commit.
2. Re-copy `site-astro/src/styles/global.css` below the header, unchanged.
3. Update the header's `Pinned commit:` and `Source sha256:` lines.
4. Run `npm run check:tokens`.
