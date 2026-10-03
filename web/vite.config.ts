/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The culture-rules HTTP API (culture_rules/server, the [server] extra). The
// browser always talks to the same-origin prefix `/api` (src/api/client.ts);
// in dev that prefix is proxied to the API, stripped, so the browser never
// needs CORS. Override the target with CULTURE_RULES_API_URL — the same
// variable the CLI reads — and it defaults to the CLI's own default.
const API_TARGET = process.env.CULTURE_RULES_API_URL ?? "http://127.0.0.1:8765";

// Dev only: the app itself never sends a credential (behind Cloudflare
// Access the edge adds Cf-Access-Jwt-Assertion). Against a local API started
// with CULTURE_RULES_INSECURE_DEV_IDENTITY=1, set CULTURE_RULES_DEV_IDENTITY
// and the dev proxy injects it as X-Culture-Identity. Unset: nothing added.
const DEV_IDENTITY = process.env.CULTURE_RULES_DEV_IDENTITY?.trim();

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
        ...(DEV_IDENTITY ? { headers: { "X-Culture-Identity": DEV_IDENTITY } } : {}),
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4174,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    css: false,
    // e2e/ is Playwright's; vitest must not try to run it.
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
