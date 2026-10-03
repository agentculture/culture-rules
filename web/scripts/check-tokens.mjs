#!/usr/bin/env node
// scripts/check-tokens.mjs
//
// Byte-identity check for src/culture-design/tokens.css, mirroring
// culture-nodes' scripts/check-culture-design.mjs (check (a)).
//
// tokens.css = a provenance header comment + a VERBATIM copy of
// agentculture/org site-astro/src/styles/global.css at a pinned commit. The
// header records the pin and the sha256 of the pinned source. This script:
//
//   1. always: hashes everything after the header and asserts it equals the
//      recorded sha256 — catches any hand edit, works offline / in CI;
//   2. when an org checkout is reachable (CULTURE_DESIGN_ORG_REPO, default
//      the sibling checkout ../org next to this repo) and holds the pinned commit: reads the source
//      AT THE PIN via `git show <pin>:<path>` (never the working tree, so
//      org's HEAD may move on) and asserts byte equality — proving the
//      recorded hash is the real org file, not merely self-consistent.
//
// Exit 0 on pass, 1 on any failure. Node stdlib + git only.

import { execFileSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const WEB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TOKENS = path.join(WEB, "src", "culture-design", "tokens.css");
const ORG_PATH = "site-astro/src/styles/global.css";
const ORG_REPO = process.env.CULTURE_DESIGN_ORG_REPO ?? path.resolve(WEB, "..", "..", "org");
const SENTINEL = "---- verbatim copy of site-astro/src/styles/global.css follows ----";

let failures = 0;
const ok = (msg) => console.log(`ok   - ${msg}`);
const fail = (msg) => {
  failures += 1;
  console.error(`FAIL - ${msg}`);
};
const sha256 = (text) => createHash("sha256").update(text, "utf8").digest("hex");

if (!existsSync(TOKENS)) {
  fail(`${path.relative(WEB, TOKENS)} does not exist`);
  process.exit(1);
}
const css = readFileSync(TOKENS, "utf8");
const pin = css.match(/^\s*\*\s*Pinned commit:\s*([0-9a-f]{40})\s*$/m)?.[1];
const recorded = css.match(/^\s*\*\s*Source sha256:\s*([0-9a-f]{64})\s*$/m)?.[1];
const at = css.indexOf(SENTINEL);
const close = at >= 0 ? css.indexOf("*/", at) : -1;
const nl = close >= 0 ? css.indexOf("\n", close) : -1;

if (!pin) fail("header has no 'Pinned commit: <40-hex sha>' line");
if (!recorded) fail("header has no 'Source sha256: <64-hex>' line");
if (nl < 0) fail(`header is missing the sentinel ("${SENTINEL}") or its closing */`);
if (failures) process.exit(1);

const body = css.slice(nl + 1);
const bodyHash = sha256(body);
if (bodyHash === recorded) ok(`tokens.css body sha256 matches the recorded ${recorded.slice(0, 12)}`);
else fail(`tokens.css body sha256=${bodyHash} != recorded ${recorded} (the verbatim section was edited)`);

let orgSource = null;
try {
  orgSource = execFileSync("git", ["-C", ORG_REPO, "show", `${pin}:${ORG_PATH}`], {
    encoding: "utf8",
    stdio: ["ignore", "pipe", "ignore"],
  });
} catch {
  console.log(`skip - org checkout ${ORG_REPO} @${pin.slice(0, 12)} not reachable; recorded hash is authoritative`);
}
if (orgSource !== null) {
  if (sha256(orgSource) === recorded) ok(`recorded sha256 is org ${ORG_PATH}@${pin.slice(0, 12)}`);
  else fail(`recorded sha256 != org ${ORG_PATH}@${pin.slice(0, 12)} sha256=${sha256(orgSource)}`);
  if (body === orgSource) ok(`tokens.css is byte-identical to org ${ORG_PATH}@${pin.slice(0, 12)}`);
  else fail(`tokens.css body differs from org ${ORG_PATH}@${pin.slice(0, 12)}`);
}

if (failures) {
  console.error(`${failures} check(s) FAILED`);
  process.exit(1);
}
console.log("tokens check: PASS");
