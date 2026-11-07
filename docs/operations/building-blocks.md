# Building blocks

Six generic builtin code steps parse, join, shift, format, read and filter
values: `json.parse`, `text.join`, `time.add`, `date.local`, `file.read` and
`list.filter`. None of them knows an app or a flow. Every format, root, shape
and rule is step config, so what a flow does with them is configuration, not
code. Each input may instead sit in the step's `config` (the input wins), and
a loop body can map `inputs.item.*` onto them with `config.with`. A failure is
final (never retried) and reads `<code>: <detail>`; with
`config.on_error: "skip"` it turns the step `skipped`, as for any step.

All six are core's: they register in `register_core`
(`culture_rules/extensions/builtin.py`) with their describe phrase and typed
ports, and the editor's Built-in picker offers them from the builtin
catalogue (below).

## `json.parse`

Parses JSON text and checks it against a shape
(`culture_rules/model/shape.py`).

| Input | Required | Meaning |
|---|---|---|
| `text` | yes | the JSON text, a string or UTF-8 bytes (at most 1 MiB); a list or an object is taken as already decoded and only checked |
| `shape` | yes | the shape spec the value must fit (usually in config) |

Output: `value`, normalised by the shape (defaults filled, text stripped or
lower-cased where the shape says so, ignored keys left out). Failures:
`bad_params` (no shape, or a malformed one), `not_json`, `shape_invalid` (the
detail names the failing field path).

A shape type also takes `nullable` (a `null` no default replaced is kept), a
`text` takes `forbid` (patterns none of which may be found), and an `object`
takes `ignore` (keys allowed but left out of the result).

```json
{
  "id": "parse",
  "kind": "code",
  "config": {
    "builtin": "json.parse",
    "shape": {
      "type": "object",
      "fields": {
        "title": {"type": "text", "strip": true, "max_chars": 120},
        "tags": {"type": "list", "items": {"type": "word", "lower": true}, "max_items": 5},
        "note": {"type": "text", "default": null, "nullable": true}
      },
      "ignore": ["debug"]
    }
  }
}
```

## `text.join`

Joins a list of scalars into one text.

| Input | Required | Meaning |
|---|---|---|
| `items` | yes | a list of strings, ints or floats (a number is written with `str()`; booleans, null, lists and objects are refused) |
| `separator` | yes | the text between items (`""` is allowed) |

Outputs: `text` and `count`. An empty list gives `""`.

```json
{"id": "join", "kind": "code", "config": {"builtin": "text.join", "separator": ", "}}
```

## `time.add`

Adds an exact elapsed duration to an instant.

| Input | Required | Meaning |
|---|---|---|
| `instant` | yes | an ISO-8601 time with a UTC offset (`Z` is fine) |
| `seconds`, `minutes`, `hours`, `days` | at least one | numbers, negative allowed, summed |

Output: `instant`, in UTC (`+00:00`). A day is exactly 86400 s on the UTC
timeline, so a day added across a daylight-saving change lands 24 hours later,
not at the same wall-clock time.

```json
{"id": "deadline", "kind": "code", "config": {"builtin": "time.add", "hours": 36}}
```

## `date.local`

Writes an instant as local time in an IANA zone.

| Input | Required | Meaning |
|---|---|---|
| `instant` | yes | an ISO-8601 time with a UTC offset |
| `tz` | yes | an IANA zone such as `Europe/Paris` (needs the system tz database) |
| `format` | yes | a `strftime` format |

Outputs: `text` (the formatted local time) and `local` (ISO-8601 with its
offset).

```json
{
  "id": "day",
  "kind": "code",
  "config": {"builtin": "date.local", "tz": "Europe/Paris", "format": "%Y-%m-%d"}
}
```

## `file.read`

Reads one local file, only under configured roots.

| Config | Meaning |
|---|---|
| `roots` (or `root`) | absolute directories (required, config only, so a run cannot widen them); the file must resolve under any one, symlinks followed first |
| `mode` | `text` (the default, UTF-8) or `json` |
| `fields` | json mode only: `{output name: dotted path}`, as `json.read` takes; a missing field is null |
| `max_bytes` | the size cap, default 1 MiB, at most 8 MiB, checked before reading |

Input: `path`, absolute or relative to the first root. Outputs: `bytes` and,
in text mode, `content`; in json mode, `value` (the whole document) or, with
`fields`, `fields`. Failures: `bad_params`, `path_refused` (missing, outside
every root, not a regular file), `too_large`, `not_text`, `not_json`.

```json
{
  "id": "settings",
  "kind": "code",
  "config": {
    "builtin": "file.read",
    "roots": ["/srv/data"],
    "mode": "json",
    "fields": {"owner": "meta.owner", "limit": "limits.daily"}
  }
}
```

## `list.filter`

Keeps the items of a list that pass configured rules, applied in this order
to each item:

| Config | Meaning |
|---|---|
| `host_from`, `host_into` | a dotted path to a URL in the item (`"."`: the item itself); an item without an `http`/`https` URL with a host is dropped, never repaired, and the lower-cased host is written to the field `host_into` on the copy `where` sees |
| `dedupe_by`, `dedupe_casefold` | a dotted path to a scalar; the first item holding a value stays, later ones with the same value are dropped (casefolded when asked) |
| `where` | a condition (`culture_rules/model/condition.py`) per item: the item is the `{"item": path}` operand, every other input is a value it reads as `{"var": name}` |
| `limit` | at most this many items are kept, the first in order (also an input) |

Input: `items` (at most 1000). Outputs: `items` (the kept originals, in
order), `count`, and how many each rule dropped: `dropped_host`,
`dropped_duplicate`, `dropped_where`, `dropped_limit`. Failures:
`bad_params`, `bad_input`.

```json
{
  "id": "keep",
  "kind": "code",
  "config": {
    "builtin": "list.filter",
    "host_from": "url",
    "host_into": "host",
    "dedupe_by": "url",
    "where": {
      "op": "and",
      "args": [
        {"op": "host_suffix", "value": {"item": "host"}, "suffix": {"var": "domain"}},
        {"op": "contains", "value": {"item": "title"}, "needle": {"literal": "release"}}
      ]
    },
    "limit": 10
  }
}
```

## Conditions over text and lists

Four condition ops join the tree (`culture_rules/model/condition.py`). They
have no CEL text form; the editor shows them read-only in words and they are
written as JSON (a step's config, the CLI or the API).

| Op | Shape | Describe words |
|---|---|---|
| `contains` | `{"op": "contains", "value": v, "needle": n}`; case-insensitive substring of a text, or member of a list | `title contains release` |
| `host_suffix` | `{"op": "host_suffix", "value": host, "suffix": domain}`; the host equals the domain or is under it (`badexample.com` is not under `example.com`) | `host is in the domain example.com` |
| `any`, `all` | `{"op": "any", "items": list, "where": node}`; `where` once per element, the element is `{"item": path}` (`"."`: itself) | `for at least one of links, its host is in the domain example.com` |

In symbols (`condition_text`, the Detailed view's lines) they read
`title contains release`, `host in domain example.com` and
`any of links: item.host in domain example.com`.

## The builtin catalogue

`GET /steps/builtins` (viewer; `operation_id` `list_builtins`) answers
`{items: [...]}`, one item per registered builtin in registration order, built
from `registry()` on every call:

- `name`: what a code step's `config.builtin` names;
- `label`: how a picker names it (the registered `label`, else its describe
  phrase, else the name);
- `inputs`, `outputs`: the typed ports an editor pre-fills, each
  `{name, type, required}` with a workflow port type; empty when the builtin
  declares none, and the editor then leaves the step's ports as they are.

The CLI verb is `culture-rules steps builtins [<name>] [--json]` (exit 2 on a
server older than 0.45.0) and the MCP tool is `steps_builtins`.

A builtin declares its label and ports where it registers, so an extension's
builtin needs no editor change:

```python
ext.builtin(
    "acme.tally",
    Lazy("acme.port", "TallyPort"),
    phrase="acme.tally: count the things",
    inputs=(("things", "array"), ("limit", "integer", False)),
    outputs=(("count", "integer"),),
)
```

Each port is a `BuiltinPortSpec` or `(name[, type[, required]])`; the type
defaults to `string` and `required` to true. The editor's picker
(`web/src/workflows/StepEditor.tsx`) reads the catalogue through
`web/src/api/builtins.ts` and keeps no table; the web tests' snapshot is
`web/src/fixtures/builtins.data.ts`
(`python3 scripts/export-builtins.py --write`, `--check` in the tests).
