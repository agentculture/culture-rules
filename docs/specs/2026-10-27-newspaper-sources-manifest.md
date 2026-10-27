# newspaper sources manifest

> Every daily newspaper issue in culture-library carries a sources manifest (each article's title, the URLs it links and a verbatim excerpt), pushed right after the issue, and the issues already in the library have one too

## Audience

- culture-library's RAG eval harness (newspaper evals refuse an issue with no manifest, culture-library eval/generate.py `_sidecar_articles`) and the operator, who reads the eval scores

## Before → After

- Before: Newspaper items in culture-library have no source URLs: the library's EPUB text extraction drops href attributes (culture-library core/ingest.py `_TextParser`.`handle_starttag` ignores attrs) and items have no source field, so evals cannot ground questions in an article's sources
- After: Each new newspaper issue in culture-library has a sources manifest within the same run that pushed it, the earlier issues have one from the backfill (skips listed), and newspaper evals can be generated for every issue

## Why it matters

- Passage-level RAG (POST /api/rag/retrieve) is scored on returning the article a question came from; without a manifest the newspaper cannot be evaluated at all (culture-rules#97)

## Requirements

- After each daily push of a newspaper, the live daily-newspaper workflow (configured in the store through the CLI, not in the PR) runs the sections step on the rendered EPUB and http.call PUTs the result to culture-library's PUT /api/library/{`item_id`}/sources (shipped: culture-library server/`routes_sources.py`), for the item id the record step returns
  - honesty: The live daily-newspaper workflow (read back with workflows describe after the change) shows the sections step and the PUT after record, fed record's `item_id` and render's EPUB; the next 05:00 run's PUT step answers 200
- Every article (section) of the issue that has at least one link gets exactly one manifest entry, and a section with no link ('No results.') gets none. This is the sections step's configured `require_links` option, not code
  - honesty: In the real-issue test (c27's), the entry count equals the number of sections with at least one result; a section rendered 'No results.' has no entry
- The manifest is built in configuration from the issue's EPUB, not from run state. A generic sections step splits the document at a configured heading tag (h2 for the newspaper) and outputs each section's heading text, its http(s) link hrefs (in order, duplicates dropped) and an excerpt; the workflow's config names the output fields as the library wants them (`article_title`, `source_urls`, excerpt). The same configuration serves the daily push and the backfill, whatever renderer version made the book
  - honesty: The live config holds the field renames (heading to `article_title`, links to `source_urls`, excerpt to excerpt); the same step config is used by the daily workflow and the backfill
- The excerpt rule is step configuration: the newspaper configures paragraphs 1 and `max_chars` 300 (cut at a word boundary), so the excerpt never crosses a paragraph and the library's 120-character probe stays inside one chunk. The step's generic part is only the text extraction: block tags become paragraph breaks, entities are unescaped and whitespace is collapsed, the same rules as culture-library's extractor
  - honesty: A contract test runs culture-library's chunking and matching rules (copied as test fixtures: block tags to breaks, collapse, chunk at 1000 on blank lines, first 120 normalised chars as a substring of one chunk) on a rendered issue and its manifest, and every entry matches
  - honesty: Before any change to the live config, a dry-run on one real stored issue shows its manifest; the operator checks it, and after the PUT, culture-library's eval generator reports unmatched = 0 for that item
- A failed manifest PUT is recorded on the run and does not fail it: the newspaper's email and library push are already done, and the step is `on_error` skip (culture-rules#97 acceptance 3)
  - honesty: With the PUT step's actor answering 500, the daily workflow run still succeeds: email sent, push recorded, the PUT step skipped with its error on the run
- A one-off backfill PUTs manifests for the newspaper issues already in culture-library (15 per #97). It is a workflow configured live from the same generic steps: `item_ids` in, then for each id http.call GET the file (saved to a file), the sections step, and http.call PUT. A failed item is skipped (`on_error` skip) and the run lists the skipped ids; it runs once with workflows run
  - honesty: The backfill's run lists every item id it was given with its outcome (pushed with an entry count, or skipped with the error), so a skip is never silent
- The PUT and the fetch are http.call requests through the library actor's http policy (allow 127.0.0.1; Authorization from grant `LIBRARY_PUSH_API_KEY`). The URL is a {{ }} template in the workflow's config over a variable for the library's base and the item id; no library route is in code
  - honesty: No URL, route or key is in code: the new code has no culture-library path; the live config holds the base URL in a variable and the key as a grant reference; scan-secrets stays clean
- Generic: http.call resolves a header value of the form `<word> grant:<NAME>` (e.g. `Bearer grant:LIBRARY_PUSH_API_KEY`) by resolving the reference and keeping the word; the value is registered with the redactor like a bare `grant:` value. Today only a whole-value `grant:` is resolved (`culture_rules/node/actions/http.py` `_header_value`)
  - honesty: A test sends a header `Bearer grant:<NAME>` through http.call with a fake secret runner and asserts the request carried `Bearer <value>`, and that the value is scrubbed from the step's output
  - honesty: A value that only contains `grant:` elsewhere (e.g. `x grant:A grant:B`, or two words before the reference) is sent unchanged or refused, never half-resolved; the accepted shape is exactly one word, one space, one reference
- Generic: http.call can save the response body to a file instead of returning it: a `save_to` option names a root the actor's http policy lists (`save_roots`); the body is streamed under a cap to `<root>/<sha256>.<ext>` atomically, and the outputs are status, path, sha256 and size, never the body (d7, as attachment.fetch does)
  - honesty: A test saves a response with `save_to` and asserts the file `<root>/<sha256>.<ext>` holds the exact bytes, the outputs are status, path, sha256 and size, and no output carries the body
  - honesty: `save_to` under a root the actor's policy does not list fails `path_refused` before any request; a symlinked root or file is refused; a body over the cap fails and leaves no file behind
  - honesty: A response whose Content-Type is outside the known set (epub, pdf, md, json, txt) is refused before anything is written; the saved extension always comes from the Content-Type
- Generic: a builtin sections step reads an EPUB (its xhtml files in name order), an HTML string or a Markdown document, given as `content_base64`, html, markdown, or `content_path` under `content_root`, and splits it at a configured heading (a tag such as h2 for EPUB/HTML, a heading level for Markdown). For each section it outputs the heading text, the http(s) links and an excerpt shaped by configured paragraphs and `max_chars`; config also sets `require_links` and renames the output fields. No I/O beyond reading the given file; standard library only
  - honesty: A test renders a real newspaper issue with newspaper.render, runs the sections step on its EPUB with the newspaper's config, and asserts one entry per section with results, none for 'No results.', the h2 text as the title and only that section's hrefs
  - honesty: Every rule the step applies (heading tag, paragraphs, `max_chars`, `require_links`, field names) comes from config or input; a test runs the same EPUB with two different configs and gets two different outputs
  - honesty: A test splits a Markdown document at a configured heading level and gets the same section shape (heading, links from \[label\](url) and bare http(s) URLs, excerpt) as for HTML

## Honesty conditions

- After rollout, GET /api/library/`<id>`/sources answers a manifest for the newest newspaper item and for every older one except the listed skips (c18, c19 hold)
- culture-library's newspaper eval generation (`generate_newspaper_set`) runs on an issue with a manifest instead of refusing it with 'has no sources sidecar'
- Before the change, GET /api/library/`<id>`/sources for a live newspaper item answers 404 `no_sources` (checked once, read-only, and recorded as evidence)
- The eval run that follows the backfill produces samples whose `source_urls` come from the manifest, scored by retrieval of the article's chunks
- The pinned newspaper bytes (tests/node/`test_epub_bytes_pinned.py`, from #112) still pass unchanged
- A manifest the sections step produces with the newspaper config passes culture-library's `validate_manifest` rules (copied as a test fixture), and the live PUT answers 200, not 422
- The diff adds no module, builtin or action kind whose name or logic names the newspaper or culture-library (grep the diff for newspaper and library in new code); the catalog lists only the three generic capabilities
- No run document on the backfill path holds EPUB bytes or base64: a test reads the stored run and asserts its outputs are scalars and the manifest list only
- Validated live, not only in tests: after the first daily issue with the new step, culture-library's newspaper eval generation for that item succeeds with unmatched = 0
- After the backfill run, a read-only loop over the newspaper items' GET /sources answers 200 for each item the run reports pushed
- Before the live wiring, one GET of an issue's file and one PUT of its manifest are made with the live key and answer 200; a 403 stops the rollout and goes back to the operator
- Evidence filed after rollout: the first daily run's PUT result, the backfill run's per-item list, and one eval generation with unmatched = 0
- The PR's changed files are code, tests, catalog, describe phrases, editor picker and docs only; any change under docs/rules is a disabled example template; the live config changes are recorded as rollout steps, not commits

## Success signals

- culture-library's newspaper eval generator (`generate_newspaper_set`) matches every manifest entry of a fresh issue to at least one chunk, with unmatched = 0
- After the backfill, GET /api/library/{id}/sources answers a manifest for every newspaper item except the listed skips

## Scope / boundaries

- The issue's EPUB and email bytes stay byte-identical: newspaper.render's output does not change, and its determinism tests stay green (`culture_rules`/node/actions/`newspaper_render.py` module doc)
- The field names are the library's as shipped: `article_title`, `source_urls`, excerpt (culture-library server/`routes_sources.py` `validate_manifest`); no alias is asked for
- No library-specific or newspaper-specific step is added. The code adds three generic capabilities (a sections step, http.call saving a response to a file, http.call resolving a grant after a scheme word); the manifest's shape, the daily wiring and the backfill are configuration
- The EPUB bytes never enter run state on the fetch path (d7): http.call saves the response body to a file under a root its actor lists, and the sections step reads it by `content_path` and `content_root`; only scalars and the manifest list are outputs
- The PR adds framework only: the three generic capabilities (c25, c26, c27), their catalog, describe phrases, editor picker and docs. The daily wiring and the backfill workflow are live configuration set through the CLI after an approved rollout; docs/rules/newspaper changes only as a disabled example template (or a docs page shows the config), never as the live behaviour

## Non-goals

- No sources manifest for transcripts now (culture-rules#97: not needed now)
- No change to culture-library: the route, its validation and its matcher are used as shipped

## Assumptions

- The library key culture-rules already holds (grant `LIBRARY_PUSH_API_KEY`, write scope, kinds newspaper and transcript) may PUT sources on newspaper items and GET their files: write implies read (culture-library core/keys.py:22) and the route checks the item's kind against the key (`routes_sources.py`)

## Scope exploration

- `s1` — `culture-library server/routes_sources.py`: PUT /api/library/{id}/sources is live on main (bb981c0): a bare list of {`article_title`, `source_urls` (non-empty, http/https), excerpt (string, <= 2000 chars)}, at most 500 entries, write scope and the key's kinds; PUT replaces, so re-pushing is idempotent
  - seeds: `c5`, `c13`
- `s2` — `culture-library eval/generate.py _match_articles`: an article matches when the first 120 whitespace-normalised, lower-cased characters of its excerpt are a substring of one chunk's text; no manifest means the newspaper eval refuses the item with a hint
  - seeds: `c8`, `c2`, `c18`
- `s3` — `culture-library core/ingest.py _TextParser + core/search.py chunk_text`: EPUB text: each xhtml file in name order, block tags (p, li, h1-h6, section, ...) become line breaks, runs of 3+ newlines collapse to a blank line, hrefs are dropped; chunks pack blank-line paragraphs up to 1000 chars, so a probe spanning two paragraphs can straddle a chunk boundary, one inside a single paragraph cannot
  - seeds: `c8`, `c3`
- `s4` — `culture_rules/node/actions/newspaper_render.py`: one chapter per section (sNN.xhtml): an h2 with the section name, then the summary bullets (links limited to the section's result URLs) or the results list (each result title linked) plus a note, or 'No results.'; chapter 1 also holds the h1 masthead; the EPUB keeps every href
  - seeds: `c6`, `c7`, `c12`
- `s5` — `docs/rules/newspaper/workflows/daily-newspaper.json`: steps: sections, claim, search, summarise, render, `email_gate`, email, `push_gate`, push, record; push is skipped when the library already has the date's issue, and record outputs the `item_id` the date is recorded with, so a manifest step after record has the id on a re-run too
  - seeds: `c5`, `c9`
- `s6` — `culture_rules/node/actions/library.py`: library.push reads every route from the actor (`upload_path`, `queue_path`, `replace_path` with {`item_id`} as one segment, `replace_method`); nothing is built in, the wire contract is fixed, the key is a grant reference resolved on the executing host
  - seeds: `c11`, `c14`
- `s7` — `docs/rules/newspaper/actors (newspaper-library)`: the bundle actor points at <http://127.0.0.1:8796> with `upload_path` /api/library, kinds newspaper and transcript, visibilities private and public, key grant:`LIBRARY_PUSH_API_KEY`; no `sources_path` or `file_path` yet
  - seeds: `c11`, `c20`
- `s8` — `culture-library core/keys.py`: write scope implies read, so the push key can GET an item's file for the backfill
  - seeds: `c20`, `c10`
- `s9` — `culture_rules/store (runs collection)`: runs carry no TTL index (only handoffs, Discord events, quarantine and interactions expire), so old newspaper runs likely still exist, but re-rendering them is not needed when the manifest is derived from the stored EPUB
  - seeds: `c10`, `c7`
- `s10` — `culture_rules/node/actions/http.py`: http.call already does PUT with a JSON body (a non-string body is sent as application/json), an exact host/IP allowlist (127.0.0.1 must be listed as an IP), `grant:` headers on the actor's policy, no redirects and a pinned connection; gaps: a header is resolved only when its whole value is `grant:<NAME>` (`_header_value`), and the body comes back as truncated text, so it cannot fetch an EPUB to a file
  - seeds: `c25`, `c26`, `c11`
- `s11` — `culture_rules/node/actions/attachment_fetch.py`: streams files to `<root>/<sha256>.<ext>` atomically under a cap (the d7 pattern), but takes no auth header and refuses loopback with no allowlist exception, so it cannot read culture-library on 127.0.0.1; its save-to-file pattern is what http.call's `save_to` reuses
  - seeds: `c26`, `c15`
- `s12` — `culture_rules/node/actions/markdown_html.py`: a generic HTML reader already exists (markdown.`from_html`: headings, paragraphs, lists, links; `drop_classes`, `item_paragraphs` as config), the precedent for a configurable, flow-agnostic document step; it outputs Markdown chunks, not sections with links
  - seeds: `c27`, `c14`

## Decisions

- `source_urls` are the links the section's text cites (the hrefs in its EPUB chapter), not every search result the model saw
- The backfill is a committed generic workflow run once with workflows run, not a CLI verb
- Supersedes c22: there is no library.sources action kind. Pushing a manifest is configuration over generic steps (the sections step and http.call), per the operator's rule that code holds generic capabilities and configuration holds behaviour
- Supersedes c23's word 'committed': the backfill workflow is configured live and run once, not committed (business logic lives in the store); c23's choice of a generic workflow over a CLI verb stands

## Hard questions

- Which extensions may `save_to` write: one taken from the response Content-Type (epub, pdf, md, json, txt), or a format the step names? (resolved: From Content-Type: map the response's Content-Type onto a known set (epub, pdf, md, json, txt) and refuse anything else (operator))
- Should the sections step also read Markdown (the transcript items are Markdown), or only EPUB and HTML for now? (c16 says no transcript manifest now) (resolved: Markdown too: the sections step reads EPUB, HTML and Markdown (operator))

## Open parks

- [unknown_nonblocking] The excerpt mirrors culture-library's EPUB text extraction (ingest.`_TextParser`, `_collapse`); if the library changes its extractor or chunker, matches can drop silently. Mitigation to ask: culture-library adds a contract test that replays a culture-rules manifest against its matcher, as it already does for library.push's bytes
