# web

The culture-rules visual editor: Vite 6 + React 18 + TypeScript, with
`@xyflow/react` 12 and `elkjs` for the graph views. It has exactly four
top-level tabs: **Workflows | Actors | Variables | Statistics**. There is no
Rules tab: rules are folded into Workflows, and each rule is shown and
edited as an entry point of the workflow it starts (spec
`docs/specs/2026-10-09-editor-rules-folded-into-workflows-three-views.md`).
This is an editor change only: rules keep their stored shape and their
endpoints. Runs, history, ledger and inbox are never top-level. They appear
in context, inside a workflow or one of its entry points.

The visual source of truth is the design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>), row 'Chosen'.
Every tab implements its 'Chosen' board; the folded Workflows tab follows
the canvas v11 boards Fold-List, Fold-Chain, Fold-Editor (Simple), WF-Flow
(Detailed) and WF-Variables (Debug). Any deliberate departure is recorded
on the PR with a screenshot.

## What is here today

- **The shell:**
  - the header (brand dot, `rules` wordmark, the four tabs, the
    signed-in avatar);
  - routing, where every other path lands on Workflows;
  - old links redirect (`src/routes/legacy-redirects.ts`): `/rules` goes to
    `/workflows`, `/rules/<id>` to `/workflows/<its workflow>?entry=<id>`,
    a rule with no workflow to `/workflows?entry=<id>`, an unknown id to
    `/workflows` with a "rule not found" notice, and, when the rules cannot
    be read, to `/workflows` with a "rules unavailable" notice that never
    calls the rule deleted. While it looks the rule up, the redirect reports
    the tab `workflows` (not yet ready);
  - the design layer (`src/culture-design/`);
  - the agent-state node.
- **Workflows** (`/workflows?id=&entry=&run=`; `/workflows/<id>` is the
  same page) holds the workflows and the rules that start them:
  - **the list** (`src/workflows/list/`): workflows linked by continuation
    rules sit on one chain card. Each workflow reads "Starts when", its
    entry points; "Continues into", the workflows its runs continue into;
    "Runs", key and budget; and "Ends with", the chain-end action. The
    counts read "N workflows · N entry points · was N rules". "See it as
    one chain" opens the Chain view (entry points, workflows and
    continuation edges). "Rules without a workflow" lists D7 candidates.
    "New workflow" and "New rule" sit at the top; "Show deleted" lists
    soft-deleted workflows to restore (or purge, for admins);
  - **chains are derived only from stored rules** (`src/fold/model.ts`): a
    continuation rule is linked to its predecessor by its condition's
    `data.workflow_id == <id>` compare. A run-event rule without that term
    is a continuation "from any workflow". The predecessor is edited with a
    dedicated control that writes exactly that compare;
  - **three views** per workflow, a switch on the toolbar
    (`src/workflows/views/`). The choice is kept per viewer in
    localStorage (`culture-rules.workflow-view`), and the editor works the
    same when storage is blocked:
    - **Simple** (the default, `src/workflows/simple/`): When / Then. When
      lists the workflow's entry points, one per rule that starts it, with
      trigger, condition, placement, attempt counting, the rule's
      exclusive group and priority (edited together; the group's other
      rules are listed with the winner marked) and the rule's
      relationships (*must run after*, *may run after*, *supersedes*);
      a continuation is owned here, by the workflow it starts. Then shows
      "Continues into" (a read-only link; the continuation is edited on
      the next workflow), "Ends here" (the chain-end action), "On
      failure" and "Runs" (key and budget);
    - **Detailed**: the steps-and-edges canvas below, compact (0.18.0):
      a card shows its machine, enable switch and name but no port rows
      (`in` / `out` show a count, "9 inputs"), and one edge joins two
      connected cards, labelled with its wire count when it carries more
      than one (`bundleEdges` in `src/workflows/model.ts`). Selecting a
      card expands its port rows and draws its wires one by one from its
      ports; while a wire is dragged, the card under the pointer expands
      too, so the drop lands on a port;
    - **Debug** (`src/workflows/views/DebugView.tsx`): every input and
      output port with its type and reference. Choosing a port lights it,
      its upstream and its downstream;
  - **every entry point keeps what the Rules tab had**: the trigger and
    action pickers and forms from `src/rules/`, the enable switch,
    history (`GET /rules/{id}/history`), the (i) "About" panel (`GET
    /rules/{id}/describe`), pending human asks ("Waiting on you"), and the
    "Stop N current runs?" notice when a rule with runs going is switched
    off (`POST /rules/{id}/stop-runs`, d17). Identity and lifecycle fields
    (id, name, description, enabled) are per entry point, never shared;
  - **shared when identical** (D3–D6): order and limits, placement, the
    action and `on_failure`, and the guard condition show once at workflow
    level when every entry point's rule holds them identically, and per
    entry point, flagged as an override, when they differ. Editing a shared
    value writes rule by rule through `PUT /rules/{id}`
    (`src/fold/writes.ts`). Before each write the rule is re-read; a rule
    changed since the edit began is skipped and flagged, never
    overwritten. The results list each rule as saved, unchanged, skipped
    or failed, and a failed one keeps its old value with a Retry;
  - **D7**: a rule with no workflow can be given a stored workflow of its
    own with no steps, and no outputs, so its trigger and condition become
    the When and its action the Then. That is two writes (`POST
    /workflows`, then `PUT /rules/{id}`): if the second fails, the new
    workflow is deleted again or shown as an orphan with a fix. "New
    rule" does this at once. After New rule or a D7 offer creates the
    workflow, the board reads "Opening `<id>`…" until the reloaded list
    holds it, then opens it. Deleting the last entry point of a stepless
    D7 workflow offers to delete the workflow too. `DELETE
    /workflows/{id}` soft-deletes a workflow even while a rule still uses
    it, so the editor first reads `GET /rules` and refuses while one does;
  - New workflow (in the head next to Import, and the empty state's
    primary action) asks only for a name and creates it with `POST
    /workflows` (no steps; a taken id moves on to `-2`, `-3`, …) and
    opens it at once; in Detailed with the step `+` focused. API errors
    show inline in the form;
  - the head renames the workflow (a draft edit, written by Save),
    enables / disables it (`POST /workflows/{id}/enable|disable`) and
    deletes it softly with Undo (`DELETE /workflows/{id}`, then `POST
    /workflows/{id}/restore`); a workflow a rule still uses is kept and
    the conflict named. The rename works in every view, Simple included;
  - **saving a trusted workflow asks first** (d6,
    `src/workflows/trusted.ts`): Save on one of the PR fixer's trusted
    workflows (`pr-fixer`, `pr-fix`, `review-commit`, `publish-fix`) opens
    "Save a trusted workflow?". It says a saved change gives the workflow a
    new digest the engine does not trust: its runs still start, but the
    fixer chain will not review or push their work until the digest is
    added to `culture_rules/actors/trusted.py` and released to every node,
    and saving the original definition back restores the trust. "Keep
    editing" (focused first, or Escape) closes it and returns to Save;
    "Save anyway" saves;
  - in Detailed, the workflow as a React Flow graph laid out by elkjs (Inputs → steps
    → Outputs) at the cards' compact height, with typed ports on the
    selected card: a drag between mismatched types is refused;
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
  - in Detailed, a step panel to edit a step, its placement and its enable
    switch; add and delete steps; save with `PUT /workflows/{id}`;
  - Run (`POST /runs` through the rule that uses the workflow) and, in
    Detailed, a run overlay from the persisted run state (`GET
    /runs/{id}`): each step's outcome and the host it ran on. Recent runs
    come from `GET /runs?workflow_id=`;
  - Import (files → `POST /import`, dry-run plan, then Apply) and Export
    (`GET /export`, one bundle download);
  - the repository picker (`GET /repos`). Its menu imports from the
    picked repository (`POST /import {repo}`) or exports into it (`POST
    /export {repo}`, a git commit); both show the dry-run plan first and
    write only on Apply.
  Code: `src/routes/Workflows.tsx`, `src/workflows/`, `src/fold/`,
  `src/rules/` (the reused rule forms and pickers), `src/api/workflows.ts`,
  `src/api/rules.ts`.
- **Actors** (`/actors?id=`) is the 'Chosen — Actors' board: a
  large-type roster with a kind filter, one row per actor, expanding
  inline to edit (`PUT /actors/{id}`), enable/disable and delete. Code:
  `src/actors/`, `src/api/actors.ts`.
- **Variables** (`/variables`) holds the shared values rules read as
  `vars.<name>`. Code: `src/routes/Variables.tsx`.
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

- **Workflows:** one stream for `workflows` (the list; an unsaved draft
  survives), `rules` (a rules change re-folds the list and the entry
  points), `runs` (recent runs, and the overlaid run), `asks` (pending
  asks) and `rule_decisions` (an entry point's last runs and skips). The
  Simple view opens no stream of its own: the tab hands each batch down
  to it (`src/workflows/simple/liveFeed.ts`);
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
  "route": "/workflows",
  "tab": "workflows",
  "identity": { "status": "signed-in", "identity": "ori", "kind": "sso", "role": "admin" },
  "errors": [],
  "workflows": {
    "count": 4, "selected": "pr-fix", "steps": ["quiet", "secrets", "threads", "sonar", "fix"], "step": null,
    "dirty": false, "run": null, "view": "simple",
    "entries": ["pr-fixer-checks", "pr-fixer-comment", "pr-fixer-review", "pr-fixer-review-comment", "pr-fixer-refix"], "entry": "pr-fixer-checks",
    "chains": 2, "without_workflow": []
  }
}
```

Each tab adds its own optional slice, typed in `src/agent-state/store.ts`:
`workflows` (`count`, `selected`, `steps`, `step`, `dirty`, `run`, plus
`view`, `entries`, `entry`, `chains` and `without_workflow` for the folded
rules; `view` is `null` where no view switch is shown: the D7 place, New
rule, New workflow and an empty list), `actors` (`count`, `shown`, `kind`, `selected`) and `statistics`
(`machines`, `offline`, `range`, `view`, `source`).

The tab `rules` and the `rules` slice (`count`, `selected`, `stages`) are a
deprecated alias kept for one release (spec c33): no view reports the tab
`rules` any more, and the Workflows tab writes the `rules` slice from the
folded rules (`selected` is the entry point asked for) so an agent reading
it keeps working. Read `workflows.entries` / `workflows.entry` instead.

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
it. The panel stays inside the window: it is never wider than the window
less a 16px gutter each side, and it moves left of its button when it would
pass the right edge. Each open re-reads the description; a reopened panel
keeps its last lines on screen until the fresh ones arrive, so it never
flashes "Reading…". A click on a row's (i) never opens the row. The lines are a `<pre>`
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
Screenshot paths can be set per suite: `RULES_SCREENSHOT` (the Workflows
board, `e2e/shell.spec.ts`, default `test-results/workflows-board.png`),
`RULES_APP_SCREENSHOT` (an entry point, `e2e/entry-points.spec.ts`,
default `test-results/entry-point.png`), `WORKFLOWS_SCREENSHOT`,
`WORKFLOWS_LIST_SCREENSHOTS` and `WORKFLOWS_CREATE_SCREENSHOTS`
(directories), `ACTORS_SCREENSHOT` and `STATISTICS_SCREENSHOT`. The fold's
own suites are `e2e/fold.spec.ts` (the PR fixer against the fake API),
`e2e/entry-points.spec.ts` and `e2e/entry-pickers.spec.ts` (the trigger
and action pickers on an entry point).

## Build integration

The wheel ships the UI. Before `uv build`, run `npm ci && npm run build`
here. The hatch hook (`hatch_build.py`) then force-includes `web/dist` as
`culture_rules/web_dist`. `culture-rules serve` serves that build at `/`,
and the API answers both at its own paths and under `/api`
(`culture_rules/server/static.py`), so the bundle's same-origin `/api`
calls work unchanged. No Node is needed at runtime.

In development, run `npm run dev` (or `npm run preview`) beside
`culture-rules serve`.
