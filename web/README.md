# web

The culture-rules visual editor: Vite 6 + React 18 + TypeScript, with
`@xyflow/react` 12 and `elkjs` for the graph views. It has exactly four
top-level tabs: **Rules | Workflows | Actors | Statistics**. Runs, history,
ledger and inbox are never top-level. They appear in context, inside a
rule or a workflow.

The visual source of truth is the design canvas
(<https://claude.ai/artifact/Jgm3JPnAhKWpeiCxFXvNBi>), row 'Chosen'.
Every tab implements its 'Chosen' board. Any deliberate departure is
recorded on the PR with a screenshot.

## What is here today

- **The shell:**
  - the header (brand dot, `rules` wordmark, the four tabs, the
    signed-in avatar);
  - routing, where every other path lands on Rules;
  - the design layer (`src/culture-design/`);
  - the agent-state node.
- **Rules** (`/rules/:ruleId?`) renders the 'Chosen — Rules' board read-only:
  - the rule list, with machine dots and enable switches;
  - the selected rule as Trigger → Condition → Workflow → Action stages;
  - a dashed relationship card for *must/may run after*;
  - the rule's last runs.

  *Planned:* editing.
- **Workflows, Actors, Statistics** are headings only. *Planned:* their
  boards.

## API

The browser calls the culture-rules HTTP API (`culture_rules/server`, the
`[server]` extra) under the same-origin prefix `/api`:

- **Dev:** `vite.config.ts` proxies `/api` to `CULTURE_RULES_API_URL`
  (default `http://127.0.0.1:8765`, the CLI's own default) and strips the
  prefix.
- **Types:** `src/api/types.ts` is hand-maintained against the committed
  `api/openapi.json`. Update both in the same PR.
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

`ready` means the view finished its first load **and** identity settled,
even when the load failed. A failed load is listed in `errors` and
rendered as an alert. It does not leave the page "loading".

## Accessibility

- A skip link opens the tab order. Every tab, switch and button is
  reachable by keyboard, and focus is visible (`tokens.css`
  `:focus-visible`).
- A run's status is announced in words. It is never carried by the dot's
  color alone.
- `prefers-reduced-motion: reduce` disables every transition (the
  `tokens.css` kill switch).
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

*Planned:* serving `web/dist` from the Python API, and a Node CI job.
Today, run the UI from `npm run dev` or `npm run preview` beside
`culture-rules` serving the API.
