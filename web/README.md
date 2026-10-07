# web

The culture-rules visual editor: Vite 6 + React 18 + TypeScript, with
`@xyflow/react` 12 and `elkjs` for the graph views. It has exactly five
top-level tabs: **Rules | Workflows | Actors | Variables | Statistics**. Runs, history,
ledger and inbox are never top-level. They appear in context, inside a
rule or a workflow.

The visual source of truth is the design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>), row 'Chosen'.
Every tab implements its 'Chosen' board. Any deliberate departure is
recorded on the PR with a screenshot.

## What is here today

- **The shell:**
  - the header (brand dot, `rules` wordmark, the five tabs, the
    signed-in avatar);
  - routing, where every other path lands on Rules;
  - the design layer (`src/culture-design/`);
  - the agent-state node.
- **Rules** (`/rules/:ruleId?`) is the 'Chosen — Rules' board, editable:
  - the rule list, with machine dots and enable switches (`POST
    /rules/{id}/enable|disable`, rolled back when refused);
  - switching a rule off while it has runs going shows a non-modal
    "Stop N current runs?" notice: Approve calls `POST
    /rules/{id}/stop-runs` and reports how many stopped, Keep running
    leaves them going (d17);
  - the focused rule as a relationship ghost → Trigger → Condition →
    Workflow → Action → `+`;
  - edit (`PUT`), delete (soft `DELETE`, with an Undo that calls
    `restore`), and creation from "New rule" (it asks "When does this happen?") then `+`;
  - *must run after*, *may run after* and *supersedes* as badges on both
    rules. Drag a rule from the list (or a card) onto a slot, or use the
    slot's picker; each card has a remove;
  - the rule's pending human asks ("Waiting on you"): `GET
    /asks?run_id=&status=open` for each of the rule's waiting runs,
    answered with `POST /asks/{id}/answer`;
  - the rule's last runs and recorded skips, newest first (`GET
    /rules/{id}/history`): a skipped rule reads `superseded by <rule>`
    (or "lost its group to", "waiting for") with an icon and a label;
  - an (i) "About" button on every list row and beside the focused
    rule's title (d19). It opens a non-modal panel anchored to the button
    with the rule as short labelled lines (`When …`, `If …`, `and …`,
    `Run …`, `On …`, `Then …`, `On failure …`, `Key …`), fetched from
    `GET /rules/{id}/describe` each time it opens. The API builds the lines
    from the config alone (no AI), so the panel shows exactly what
    `culture-rules rules describe` prints. See "The (i) panel" below.
  Code: `src/routes/Rules.tsx`, `src/rules/`, `src/api/rules.ts`.
- **Workflows** (`/workflows?id=&run=`) is the 'Chosen — Workflows' board:
  - New workflow (in the head next to Import, and the empty state's
    primary action) asks only for a name, creates it with `POST
    /workflows` (no steps; a taken id moves on to `-2`, `-3`, …) and opens
    it on the canvas with the step `+` focused. API errors show inline in
    the form;
  - the head renames the workflow (a draft edit, written by Save),
    enables / disables it (`POST /workflows/{id}/enable|disable`) and
    deletes it softly with Undo (`DELETE /workflows/{id}`, then `POST
    /workflows/{id}/restore`); a workflow a rule still uses is kept and
    the conflict named;
  - the workflow as a React Flow graph laid out by elkjs (Inputs → steps
    → Outputs), with typed ports: a drag between mismatched types is
    refused;
  - zoom (d19), from 25% to 200%: a pinch or ctrl/cmd + wheel, the
    on-canvas Zoom out / Zoom in / Fit to width buttons (with the level
    read out), or `+` / `-` / `0` while focus is in the canvas. A plain
    wheel never zooms, so the page keeps scrolling. The canvas is sized
    like a document at its zoom, so Fit shrinks a wide graph to the canvas
    width and returns to 100% when the graph already fits
    (`src/workflows/zoom.ts`);
  - an (i) "About" button on every list row and beside the open
    workflow's title (d19): the stored workflow's steps, numbered, a loop's
    body indented (`GET /workflows/{id}/describe`, the same lines as
    `culture-rules workflows describe`). While the draft has unsaved edits
    the head's panel says it shows "the saved version". The button makes
    the head's row full at 1280px beside the list, so Run may wrap under
    it there;
  - a step panel to edit a step, its placement and its enable switch; add
    and delete steps; save with `PUT /workflows/{id}`;
  - Run (`POST /runs` through the rule that uses the workflow) and a run
    overlay from the persisted run state (`GET /runs/{id}`): each step's
    outcome and the host it ran on. Recent runs come from `GET
    /runs?workflow_id=`;
  - Import (files → `POST /import`, dry-run plan, then Apply) and Export
    (`GET /export`, one bundle download);
  - the repository picker (`GET /repos`). Its menu imports from the
    picked repository (`POST /import {repo}`) or exports into it (`POST
    /export {repo}`, a git commit); both show the dry-run plan first and
    write only on Apply.
  Code: `src/routes/Workflows.tsx`, `src/workflows/`,
  `src/api/workflows.ts`.
- **Actors** (`/actors?id=`) is the 'Chosen — Actors' board: a
  large-type roster with a kind filter, one row per actor, expanding
  inline to edit (`PUT /actors/{id}`), enable/disable and delete. Code:
  `src/actors/`, `src/api/actors.ts`.
- **Statistics** (`/statistics`) is the 'Chosen — Statistics' board: one
  lane per enrolled machine, offline ones included. Each lane shows load,
  the steps it runs and its queue depth (`GET /machines/status`), and
  runs per time bucket with ok/failed counts (`GET /runs`; a run belongs
  to the `hosts` its steps ran on). Controls: range 1h/24h/7d and a table
  view. The board refreshes live (below) and re-reads `/machines/status`
  every 10 s, since heartbeats change load without a write it would see; a
  machine whose last heartbeat is older than 30 s turns offline. When
  `/machines/status` fails, the lanes are derived from runs, say so, and
  the failure is listed. Code: `src/statistics/`,
  `src/api/statistics.ts`.

## API

The browser calls the culture-rules HTTP API (`culture_rules/server`, the
`[server]` extra) under the same-origin prefix `/api`:

- **Dev:** `vite.config.ts` proxies `/api` to `CULTURE_RULES_API_URL`
  (default `http://127.0.0.1:8765`, the CLI's own default) and strips the
  prefix.
- **Types:** `src/api/types.ts` is hand-maintained against the committed
  `api/openapi.json`. Update both in the same PR.
- **Calls:** `src/api/client.ts` owns the one `request` helper (plus
  `getJson` and `items`); every tab adapter (`src/api/<tab>.ts`) builds
  on it.
- **Credentials:** the app never attaches one. Behind Cloudflare Access
  (the loopback listener), the edge adds `Cf-Access-Jwt-Assertion` to
  every same-origin request.
- **Dev identity:** for a local API started with
  `CULTURE_RULES_INSECURE_DEV_IDENTITY=1`, set
  `CULTURE_RULES_DEV_IDENTITY=<name>` when running `npm run dev`. The vite
  proxy then injects it as `X-Culture-Identity`. Unset, the proxy adds
  nothing.
- **Identity:** comes from `GET /whoami` (the `WhoAmI` schema:
  `identity`, `kind`, `roles`), read once per session
  (`src/hooks/useWhoami.ts`). The display name is `identity`. The
  effective role is the highest of `roles` (viewer < editor < admin). A
  401 is "not signed in". Any other failure is "identity unavailable";
  no identity is ever invented.

## Live updates

`src/api/live.ts` `useLiveUpdates(collections, onChange)` keeps one
EventSource on `/api/events/stream?collections=...` (the API's SSE
fan-out) and hands each coalesced batch of changes to the view, which
refetches what it shows:

- **Rules:** `rules` (the list), `runs` and `asks` (pending asks),
  `runs` and `rule_decisions` (last runs and skips);
- **Workflows:** `workflows` (the list; an unsaved draft survives) and
  `runs` (recent runs, and the overlaid run);
- **Statistics:** `machines`, `runs` and `heartbeats`.

A stream the browser retries itself resumes with `Last-Event-ID`; one it
gave up on is reopened with backoff and `?after=<last event id>`. Under
vitest the hook is off unless a factory is injected
(`setLiveSourceFactory`). The short refresh cue (`data-live-flash`) never
animates under `prefers-reduced-motion: reduce`.

## The agent-state node

The root renders one `<script type="application/json" id="agent-state">`:

```json
{
  "status": "loading | ready",
  "view_ready": true,
  "route": "/rules/build-and-publish",
  "tab": "rules",
  "identity": { "status": "signed-in", "identity": "ori", "kind": "sso", "role": "admin" },
  "errors": [],
  "rules": { "count": 5, "selected": "build-and-publish", "stages": ["trigger", "condition", "workflow", "action"] }
}
```

Each tab adds its own optional slice, typed in `src/agent-state/store.ts`:
`rules`, `workflows` (`count`, `selected`, `steps`, `step`, `dirty`,
`run`), `actors` (`count`, `shown`, `kind`, `selected`) and `statistics`
(`machines`, `offline`, `range`, `view`, `source`).

`ready` means the view finished its first load **and** identity settled,
even when the load failed. A failed load is listed in `errors` and
rendered as an alert. It does not leave the page "loading".

## The (i) panel

`src/components/AboutButton.tsx` (d19) replaces an always-visible
description: progressive disclosure keeps the boards as the design canvas
draws them, and the (i) costs one small control per row. The button is
36px with the 44px hit area of d4, named "About" plus the rule or workflow name, with
`aria-expanded` and `aria-controls`. Enter or Space opens it, focus moves
into the panel (`role="dialog"`, `aria-modal="false"`), and Escape closes
it and returns focus to the button, as Close does. A press outside closes
it. A click on a row's (i) never opens the row. The lines are a `<pre>`
in the mono face, so indentation and the symbols `∈ ≠ × ≤` read exactly
as the CLI prints them. Copy puts them on the clipboard. The panel has no
motion, and at phone width (640px and under) it docks to the bottom of
the screen. A failed call shows its error in the panel.

## Accessibility

- A skip link opens the tab order. Every tab, switch and button is
  reachable by keyboard, and focus is visible (`tokens.css`
  `:focus-visible`).
- Every button, link, switch, tab, radio and select has a hit area of at
  least 44x44 px while keeping the canvas's visual size (deviation d4,
  `src/styles/hit-area.css`; pinned by `e2e/hit-area.spec.ts`).
- A run's status is announced in words. It is never carried by the dot's
  color alone.
- `prefers-reduced-motion: reduce` disables every transition (the
  `tokens.css` kill switch), and a button or key zoom on the workflow
  canvas then lands at once instead of animating.
- The workflow canvas zooms from the keyboard: `+`, `-` and `0` (fit)
  while focus is in it, never while typing in a field. The zoom buttons
  are labelled and announce the new level.
- The Playwright suite runs axe on every tab and requires zero serious or
  critical violations.

## Commands

```bash
npm ci                 # install (package-lock.json is committed)
npm run dev            # vite on :5173, /api proxied
npm run build          # tsc -b && vite build  ->  web/dist (gitignored)
npm run preview        # serve the build on 127.0.0.1:4174
npm test               # vitest
npm run test:e2e       # playwright (chromium; `npx playwright install chromium`)
npm run check          # tokens byte-identity + chart palette validation
```

The e2e suite serves the API by request interception (`e2e/fixtures/api.ts`
over `src/fixtures/rules-fixture.ts`). No Python server, no store.
`RULES_SCREENSHOT=<path>` sets where the Rules screenshot is written.

## Build integration

The wheel ships the UI. Before `uv build`, run `npm ci && npm run build`
here. The hatch hook (`hatch_build.py`) then force-includes `web/dist` as
`culture_rules/web_dist`. `culture-rules serve` serves that build at `/`,
and the API answers both at its own paths and under `/api`
(`culture_rules/server/static.py`), so the bundle's same-origin `/api`
calls work unchanged. No Node is needed at runtime.

In development, run `npm run dev` (or `npm run preview`) beside
`culture-rules serve`.
