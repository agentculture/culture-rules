import { defineConfig, devices } from "@playwright/test";

const PORT = 4174;
const BASE_URL = `http://127.0.0.1:${PORT}`;

/**
 * The e2e suite drives the *built* bundle through `vite preview` with the
 * culture-rules API served by Playwright request interception
 * (e2e/fixtures/api.ts). No Python server, no store, no network: the walk
 * tests the UI's contract with the API shape (api/openapi.json).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: BASE_URL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 880 } },
    },
  ],
  webServer: {
    command: `npm run preview -- --port ${PORT} --strictPort`,
    url: BASE_URL,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
