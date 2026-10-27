# A sources manifest for a pushed document (culture-rules#97)

Some libraries want, beside each pushed document, a manifest of its parts:
each part's title, the URLs it links and a short verbatim excerpt. This page
shows how to build that manifest in configuration, from three generic
capabilities, and how the daily newspaper can use it. It is a **worked
example**. The PR that added the capabilities does not apply it: the live
flows are configured in the store through the CLI, never committed, and the
committed newspaper bundle is unchanged.

## The three generic capabilities

| Capability | What it does | Guide |
|---|---|---|
| `Bearer grant:<NAME>` header values | `http.call` resolves a header of the form `<word> grant:<NAME>` and keeps the word | [http.md](../actors/http.md) |
| `save_to` on `http.call` | streams a response body to `<root>/<sha256>.<ext>` under a root the actor's policy lists, and returns only `status`, `path`, `sha256`, `size`, `content_type` | [http.md](../actors/http.md) |
| `document.sections` | a code builtin that splits an EPUB, an HTML page or a Markdown document at a heading and outputs each section's heading, links and excerpt | below |

### `document.sections`

A step with `config.builtin: "document.sections"` reads exactly one source, as
an input (or a config key; the input wins):

- `content_base64`, an EPUB's bytes;
- `html`, a page; `markdown`, a document;
- `content_path` with `content_root`, a file under a root (`..` cannot leave it,
  and a symlink anywhere below the root is refused `path_refused`). `format`
  (`epub`, `html` or `markdown`) overrides the file's extension.

| Config | Meaning |
|---|---|
| `split_on` (required) | the heading that starts a section: `h1` to `h6`, or a level 1 to 6. A section runs to the next heading of the same or a higher rank |
| `paragraphs` (required) | the excerpt holds the section's first N paragraphs, 0 to 50 |
| `max_chars` (required) | the excerpt is cut to at most this many characters, at the last space (1 to 20000) |
| `require_links` | drop sections that cite no `http`/`https` link (default false) |
| `fields` | output names for `heading`, `links` and `excerpt`, e.g. `{"heading": "article_title"}` |

Outputs: `sections` (a list, in document order, each entry under the names in
`fields`), `count` and `found` (the sections before `require_links` dropped
any). The EPUB's bytes and text never enter run state. The step is
deterministic: the same input and config always give the same output.

## Example: a manifest for each daily newspaper issue

After the `record` step of the live `daily-newspaper` workflow, two more steps
build and send the manifest. The values below are placeholders: the base URL
is a variable, the key is a grant reference, and the loopback address stands
for wherever the library listens.

**The library actor** (a `service` actor the `library.push` step already
uses) gains an `http` policy. The `Authorization` header is a reference, never
a value:

```json
{
  "http": {
    "allow": ["127.0.0.1"],
    "headers": {"Authorization": "Bearer grant:LIBRARY_PUSH_API_KEY"},
    "save_roots": ["/srv/culture-rules/fetched"]
  }
}
```

**A variable** `library_base` holds the library's base URL, for example
`"http://127.0.0.1:8080"`, seeded with
`culture-rules variables set library_base --value '"http://127.0.0.1:8080"' --apply`.
The rule maps it into a workflow input with `{"$var": "library_base"}`, the way
it maps `newspaper_device`.

**Step 1, `sections`** (kind `code`), fed by `render`'s `epub_base64` (wired to
an input `content_base64`):

```json
{
  "id": "manifest",
  "kind": "code",
  "config": {
    "builtin": "document.sections",
    "split_on": "h2",
    "paragraphs": 1,
    "max_chars": 300,
    "require_links": true,
    "fields": {"heading": "article_title", "links": "source_urls", "excerpt": "excerpt"}
  }
}
```

`split_on: "h2"` makes each newspaper section one entry, `paragraphs: 1` keeps
the excerpt inside one paragraph, `max_chars: 300` keeps it short, and
`require_links` leaves out a section that says "No results." The `fields`
rename gives the names the library's manifest wants.

**Step 2, `put_manifest`** (kind `code`, builtin `action`), fed by `manifest`'s
`sections`, `record`'s `item_id` and the workflow input `library_base`:

```json
{
  "id": "put_manifest",
  "kind": "code",
  "config": {
    "builtin": "action",
    "on_error": "skip",
    "action": {
      "kind": "http.call",
      "params": {
        "actor": "newspaper-library",
        "method": "PUT",
        "url": "{{ inputs.library_base }}/api/library/{{ inputs.item_id }}/sources",
        "body": "inputs.sections"
      }
    }
  }
}
```

A reference resolves against the step's input ports only, so `library_base`,
`item_id` and `sections` are input ports of the step, each wired by an edge.
`on_error: "skip"` turns a failed PUT into a skipped step with its error on the
run: the email and the push are already done, so the run still succeeds. A
`describe` of the workflow (`culture-rules workflows describe daily-newspaper`)
shows both steps after `record` once it is applied.

Before applying it, run the `document.sections` step on one stored issue and
read the manifest it gives. Apply with `--apply` only after you have checked
it.

## Example: backfilling the issues already pushed

A one-off workflow, run once with `workflows run`, fills in the earlier
issues. It takes `item_ids` (a list of objects, each `{"id": 11}`) and
`library_base`, and loops over them with a `for_each` (`config.items` is
`item_ids`; the step reads `inputs.item.id`). Each pass of the body runs three steps, each with
`on_error: "skip"` so a failed item is skipped and listed, never silent:

1. **`fetch`**, `http.call` `GET` on
   `{{ inputs.library_base }}/api/library/{{ inputs.item.id }}/file` through the
   library actor, with `"save_to": "/srv/culture-rules/fetched"`. Its outputs
   are `path` and `sha256`, not the file.
2. **`manifest`**, `document.sections` with `content_path` from `fetch`'s
   `path` and `content_root` `/srv/culture-rules/fetched`, and the same config as
   the daily step above (so both flows give the same entries whatever renderer
   made the book).
3. **`put_manifest`**, `http.call` `PUT` on
   `{{ inputs.library_base }}/api/library/{{ inputs.item.id }}/sources` with
   `body` set to `inputs.sections`.

The loop declares an `outcomes` output port and collects each pass's entry
count from the `manifest` step:

```json
{
  "id": "each",
  "kind": "for_each",
  "max_iterations": 20,
  "config": {"items": "item_ids", "collect": {"count": "manifest.count"}},
  "outputs": [
    {"name": "results", "type": "array"},
    {"name": "outcomes", "type": "array"}
  ]
}
```

and the workflow exports it with an output
`{"name": "outcomes", "source": "steps.each.outputs.outcomes", "type": "array"}`.

Run it:

```bash
culture-rules workflows run backfill-manifests --inputs-json @inputs.json --apply
```

with `inputs.json` holding
`{"item_ids": [{"id": 11}, {"id": 99}, {"id": 12}], "library_base": "..."}`.
The run's `outcomes` output lists every id it was given, in order, one entry
per id:

```json
[
  {"index": 0, "item": {"id": 11}, "status": "succeeded", "errors": {}, "count": 2},
  {"index": 1, "item": {"id": 99}, "status": "skipped", "count": null,
   "errors": {
     "fetch": "http_status: server answered 404",
     "manifest": "input 'content_path' (string) is missing",
     "put_manifest": "input 'sections' (array) is missing"}},
  {"index": 2, "item": {"id": 12}, "status": "succeeded", "errors": {}, "count": 2}
]
```

A pushed id is `succeeded` with its entry `count`; a failed id is `skipped`,
and `errors` names the step that failed with its error and each step after it
that was skipped for want of its input (`input_missing`). The rules, for any
`for_each` loop:

- `status` is `succeeded` when every body step of the pass succeeded, and
  `skipped` when any was skipped. A failed body step (one without
  `on_error: "skip"`) still fails the loop and the run.
- `errors` maps each skipped body step that kept an error to its message. A
  step skipped by a false `when`, or a disabled one, has no error: the pass is
  `skipped` with nothing in `errors` for it.
- `config.collect` (`{name: "<body step id>.<output>"}`) adds each name to
  every entry with that step's output, copied as is (so collect small
  values), and `null` when the step did not output it. A name may not be
  `index`, `item`, `status` or `errors`. Saving the workflow, or starting a
  run, refuses a collect that is not on a `for_each` loop with an `outcomes`
  port, a path that is not `<step>.<output>`, a step that is not in the loop's
  body, or an output the step does not declare.

An entry never holds a body step's whole outputs; the loop's `results`
output still holds one object per id (`{}` for a skipped one), and the
run's step states (`each[1]/fetch` and so on) keep each step's own record.

## What to check

- `culture-rules workflows describe daily-newspaper` shows the `manifest` step
  and the PUT after `record`.
- A PUT that answers an error leaves the run `succeeded` with the step
  `skipped`.
- No stored run holds EPUB bytes or base64 on the backfill path: the fetch
  outputs a path, the sections step outputs a list of small objects.
