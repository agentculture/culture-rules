#!/usr/bin/env node
// scripts/check-palette.mjs
//
// Runs the dataviz palette validator (scripts/validate_palette.js, vendored
// with provenance) over the chart palette in src/culture-design/chart.ts:
// the light set against the light surfaces, the dark set against the dark
// surfaces of tokens.css (--surface / --bg). Also pins the light set to the
// design canvas's machine colors. Exit 1 if any run FAILs.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CHART = path.join(WEB, "src", "culture-design", "chart.ts");
const VALIDATOR = path.join(WEB, "scripts", "validate_palette.js");
const REQUIRED_LIGHT = ["#0a8a78", "#b4531f", "#3b4fb0"];
// tokens.css: light --surface / --bg, dark --surface / --bg.
const SURFACES = { light: ["#ffffff", "#f4f5fb"], dark: ["#161b36", "#0b0f20"] };

let failures = 0;
for (const f of [CHART, VALIDATOR]) {
  if (!existsSync(f)) {
    console.error(`FAIL - ${path.relative(WEB, f)} does not exist`);
    failures += 1;
  }
}
if (failures) process.exit(1);

const source = readFileSync(CHART, "utf8");
function set(mode) {
  const m = source.match(new RegExp(`${mode}:\\s*\\[([^\\]]*)\\]`));
  return m ? [...m[1].matchAll(/"(#[0-9a-fA-F]{6})"/g)].map((x) => x[1].toLowerCase()) : [];
}
const palette = { light: set("light"), dark: set("dark") };

if (JSON.stringify(palette.light) !== JSON.stringify(REQUIRED_LIGHT)) {
  console.error(`FAIL - light set ${palette.light} != design canvas ${REQUIRED_LIGHT}`);
  failures += 1;
}
if (palette.dark.length !== palette.light.length) {
  console.error(`FAIL - dark set has ${palette.dark.length} slots, light has ${palette.light.length}`);
  failures += 1;
}

for (const mode of ["light", "dark"]) {
  for (const surface of SURFACES[mode]) {
    try {
      const out = execFileSync(
        process.execPath,
        [VALIDATOR, palette[mode].join(","), "--mode", mode, "--surface", surface],
        { encoding: "utf8" },
      );
      console.log(out.trim());
    } catch (err) {
      console.error(String(err.stdout ?? err));
      console.error(`FAIL - ${mode} palette on ${surface}`);
      failures += 1;
    }
  }
}

if (failures) {
  console.error(`${failures} palette check(s) FAILED`);
  process.exit(1);
}
console.log("palette check: PASS");
