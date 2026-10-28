# State between runs

> Workflows can keep a named set of keys between runs: generic builtins read the set (dropping items already in it) and add keys to it after a run's work succeeds, each key expiring after a configured window; the first configured use is the daily newspaper not repeating articles

## Audience

- Flow authors who configure workflows live through the CLI (the operator, for the newspaper first); and the newspaper's reader, who sees fewer repeated links

## Before → After

- Before: Today nothing a run learns reaches the next run except admin-written shared variables and cursor.advance's single string; the newspaper runs the same queries each morning and links what Brave returns, so on 2026-10-28 11 of its 13 links had already appeared on 10-26 or 10-27
- After: The daily newspaper does not link an article (by URL) that an earlier delivered issue linked within the configured window; on 2026-10-28, 11 of 13 links had already appeared on 10-26 or 10-27

## Requirements

- Generic builtin keyset.add puts keys into a named set, one store document per key with its first-seen time and `expires_at`, written insert-if-absent so a retry or a concurrent add leaves one document and keeps the first-seen time; the set name, the key field, the normalisation, the window and `max_keys` are step config, never code
  - honesty: keyset.add retried with the same keys leaves exactly one document per key, and two concurrent adds of the same key both succeed with one document left
  - honesty: the set name, key field, normalisation, window and `max_keys` are read from step config or inputs; a grep of the new modules finds no newspaper, url-parameter or window constant
- Generic builtin keyset.filter takes a list and returns, in order, the items whose key (a configured field, after the configured normalisation) is not in the named set within the window, plus the counts kept and dropped; the set's keys never enter run state; it names no app, flow or field in code
  - honesty: keyset.filter keeps item order, keeps items with no key field (or an empty one) and reports them, and its outputs hold the kept list and counts but never the stored keys
- The set is bounded and expires: one store document per key carries `expires_at` under a TTL index (operator q1), the window is config, and a configured cap on keys per add keeps one run from flooding it
  - honesty: a key older than the window is not seen by keyset.filter even before the TTL monitor deletes it, and its document carries `expires_at` so the TTL index removes it
  - honesty: keyset.add refuses (`bad_params`) a call over `max_keys` rather than truncating silently
- Only finished work marks keys seen: the add step is placed in the workflow (configuration) after the flow's own commit point, so a failed or skipped run leaves the set unchanged; for the newspaper that placement is after record, set live
  - honesty: in a run that fails or skips before the add step, the set is unchanged afterwards; the flow test shows it
- Each new builtin is registered in node/runner.py, has a describe phrase in `BUILTIN_WORDS` (model/describe.py), and a guide under docs/; the describe fixtures stay green
  - honesty: both builtins are registered in node/runner.py and have `BUILTIN_WORDS` phrases; the describe fixtures and tests/model/`test_describe.py` pass
- Generic key normalisation is declarative step config, with no built-in list: e.g. lowercase the host, drop the fragment and a trailing slash, and drop query parameters matching configured patterns (the newspaper configures `utm_`\* and ref); with no normalisation configured, the key is the field value unchanged
  - honesty: with no normalisation configured, the key is the field value unchanged; with the newspaper's config, <http://Example.com/a/?utm_source=x#f> and <https://example.com/a> give the same key
- Admin verbs over the sets: a keysets noun with list (set names with key counts), show (one set's keys with seen and expiry times) and clear (one key, or the whole set; dry run by default, --apply commits), as API routes pinned in api/openapi.json, CLI verbs, MCP tools, explain entries and learn text, with the parity test green; reads need admin too, since keys may be private URLs
  - honesty: list, show and clear answer 403 for a non-admin token; clear without --apply changes nothing; the openapi contract and surface parity tests pass

## Honesty conditions

- a workflow built only from keyset.filter and keyset.add, with no newspaper builtin involved, drops on its second run the items its first run added
- the live daily-newspaper, configured through the CLI only, renders no link whose normalised form an issue of the last 14 days already linked
- a flow author can wire both builtins from the step config alone, with the guide and the describe phrases, without reading the source
- no code path from a node step writes the variables collection, and the explain and editor text 'only an admin may write' stays true
- `TRUSTED_WORKFLOWS_V2`, model/validate.py and model/`action_kinds.py` are unchanged by the diff
- `newspaper_render.py`, newspaper.py and `newspaper_issue.py` are unchanged by the diff, and the byte pins in tests/node/`test_epub_bytes_pinned.py` pass
- the 11-of-13 figure is reproducible from runs run-498cad51, run-90056447 and run-ada99021 by extracting href links from the email step's html
- the 7-issue count is computed by one script over the stored runs, normalised with the same settings the live step uses

## Success signals

- Over the first 7 consecutive daily issues after go-live, 0 links (normalised as configured) appear that an issue in the previous 14 days already linked, counted from the stored runs' rendered html; the number of sections showing No results is reported alongside, not gated

## Scope / boundaries

- Shared variables stay admin-written through the API; runs do not write the variables collection (it is versioned per write, unbounded and global, and the editor and explain text say only an admin may write)
- Untouched: trust (`TRUSTED_WORKFLOWS_V2` gates only github.push and review), save-time validation of builtin configs (none exists; builtins check inputs at run time, `bad_params`), and action kinds (a builtin code step is not one)
- newspaper.render is unchanged: a section the filter left empty renders its existing No results. line (`newspaper_render.py`:24,96); wording other than that would be a separate render input, out of scope

## Non-goals

- No flow-specific code: everything the newspaper needs is live workflow configuration: the set name, the 14-day window, the key field (url), the normalisation settings, which steps read and add, the raised search count, and the placement after record. The committed bundle may gain these steps only as a disabled template; newspaper.filter, newspaper.render and newspaper.issue are unchanged

## Assumptions

- A raised search count (config, e.g. 10 to 20 per query) leaves enough fresh results that most sections still have links; Brave's free tier allows it within the existing quota; not yet measured

## Scope exploration

- `s1` — `live runs run-498cad51 / run-90056447 / run-ada99021 (issues 10-28, 10-27, 10-26)`: measured: 13, 14 and 17 links; 10 shared between each pair of days; 11 of 10-28's 13 links already ran on 10-26 or 10-27
  - seeds: `c2`
- `s2` — `culture_rules/node/actions/cursor.py:94-114, discord_sweep.py:24-25`: cursor.advance is the run-written precedent: a read step outputs the stored value, acting steps run, a final CAS step (store.`update_if`) writes only after success; but it holds one string of at most 200 chars, no set, no TTL
  - seeds: `c4`
- `s3` — `culture_rules/node/actions/newspaper.py:269-286,310-332 (newspaper.filter)`: dedupe today is per section only (a local seen set, first URL wins); no input takes an exclude list; there is no generic list-filter builtin in the runner.py registry (~400-424)
  - seeds: `c5`
- `s4` — `culture_rules/store/mongo.py:749-769 ensure_index; actors/handoff.py:168,321; events/hook_sink.py:118`: a generic TTL index helper exists (`ttl_seconds` on an `expires_at` field), used by handoffs and hook-sink events; variables, `sweep_cursors`, checklists and `fixer_cooldowns` have no TTL or retention
  - seeds: `c6`
- `s5` — `culture_rules/node/actions/newspaper_issue.py:446 (_record), live daily-newspaper rev 6 step order`: record marks the date delivered by CAS; steps after it run only when deliver is true; the add step belongs after record, fed from the links render actually used
  - seeds: `c7`
- `s6` — `culture_rules/model/variable.py:25-60, store/mongo.py:627-675, auth/policy.py:11-12, explain/catalog.py:238, web/src/routes/Variables.tsx`: variables are admin/API-write only, append a version on every put (one doc grows toward 16 MB), have no list cap, and no node action writes them
  - seeds: `c8`
- `s7` — `memory steps-generic-flows-are-config; CLAUDE.md 'Library items' (flows configured live, never committed)`: operator rule: code holds generic capabilities, flows and business logic are configuration; extending newspaper.filter with an exclude input would put the rule in a newspaper builtin
  - seeds: `c9`
- `s8` — `culture_rules/actors/trusted.py:75,122,262; model/validate.py:936-949; model/action_kinds.py`: cursor.advance and fixer.cooldown already write state untrusted; validate.py checks only config.with for builtins; `action_kinds.py` covers kind: action steps only
  - seeds: `c10`
- `s9` — `culture_rules/node/runner.py ~400-425 builtins dict; model/describe.py:67; tests/model/test_describe.py:237`: a builtin without a `BUILTIN_WORDS` entry describes as 'builtin X'; registration is a Port class plus a dict entry
  - seeds: `c11`
- `s10` — `operator q3 decision; culture_rules/node/actions/newspaper.py:269-286 (dedupe by exact URL today)`: the same article reappears under tracking parameters; which parameters to drop is a newspaper decision, so it is config passed to a generic normaliser, not a list in code
  - seeds: `c12`
- `s11` — `culture_rules/node/actions/newspaper_render.py:13,24-25,96`: render already prints `NO_RESULTS` = 'No results.' for an empty section and asks for no summary, so 'empty rather than repeat' (operator q4) needs no code
  - seeds: `c13`

## Decisions

- Operator 2026-10-28: the set lives in a new collection with one document per key under a TTL index; admin read and clear verbs ship in the first release; the newspaper keys on a normalised URL for 14 days; a section emptied by the filter shows No results rather than repeats; the builtins are keyset.filter and keyset.add

## Hard questions

- What is the key and window for the newspaper: the exact URL, or a normalised one (scheme, trailing slash, `utm_`\* dropped)? And how many days (7? 14? 30?)? (resolved: Operator 2026-10-28: a normalised URL (lowercase host; scheme, fragment, trailing slash and `utm_`\*/ref query parameters dropped) for 14 days; the normalisation and the window are step config)
- When every result in a section was already seen, does the section render empty ('nothing new'), or fall back to repeats, or does the search ask for more results (count) to make up for the filtered ones? (resolved: Operator 2026-10-28: ask for more, then empty: the search count is raised (config) so filtered results leave enough fresh ones; a section with none left says nothing new today rather than repeating)
- Where does the set live: a new store collection of its own (one doc per set, or one doc per key with an `expires_at` TTL index), or the shared variables collection made node-writable? (resolved: Operator 2026-10-28: a new store collection, one document per key with an `expires_at` TTL index plus the set name, written by compare-and-set like cursor.advance; shared variables stay admin-only)
- Is the set inspectable and editable by the operator (an API route plus CLI/MCP verbs, e.g. to clear it), or node-internal in the first release? (resolved: Operator 2026-10-28: read and clear in the first release: an API route plus CLI/MCP verbs to list a set's keys and clear one key or the whole set (admin))
- One generic list-exclude builtin between search and filter, or should the set-read builtin itself take a list and return only the unseen items (one step instead of two)? (resolved: Operator 2026-10-28: two builtins, keyset.filter (a list in, the items whose normalised key is not in the set within the window out, plus counts) and keyset.add (keys in after the commit point); no separate read builtin, so the set's keys never enter run state)
