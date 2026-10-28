# Keysets: state between runs

A keyset is a named set of keys that a workflow remembers between runs, each key for a
window. Two generic builtins use it: `keyset.filter` drops the items whose key a set
already holds, and `keyset.add` remembers the items' keys. Everything about a particular
flow (which set, which field, how long, how to normalise) is step configuration; the code
knows no flow (spec `docs/specs/2026-10-28-state-between-runs.md`).

## The store

- One document per key in the `keysets` collection: `{id, set, key, first_seen,
  expires_at}`, where `id` is the sha256 of the set name and the key, so a long key never
  becomes a document id.
- A TTL index on `expires_at` deletes a document once its window has passed. Until the
  monitor runs, `keyset.filter` ignores an expired key itself, so the window is exact.
- A set name is 1 to 100 letters, digits, `_`, `:` or `-`. It is one namespace: two
  workflows that name the same set share its keys on purpose.
- A key is at most 2048 characters after normalisation.

## `keyset.filter`

A code step with `config.builtin: keyset.filter`.

| Setting | Meaning |
|---------|---------|
| `set` | the set name |
| `key_field` | the item field that holds the key (for example `url`) |
| `normalise` | normalisation rules (below); `{}` for none |
| `window_days` | how far back a key counts as seen |

Input `items`: a list of objects, or a list of such lists (what a `for_each` loop yields,
one list per iteration). The output `items` has the same shape and order, without the
items whose normalised key the set holds within the window. An item with no key field,
or an empty one, is kept. The outputs also carry `kept`, `dropped` and `keyless` counts,
never a stored key. It writes nothing.

## `keyset.add`

A code step with `config.builtin: keyset.add`: the same `set`, `key_field`, `normalise`
and `window_days`, plus `max_keys`, the most keys one call may add. Input `items` takes
the same shapes. Each item's normalised key is stored until now plus `window_days`; a key
already there keeps its first-seen time, so a retried step adds nothing twice. More keys
than `max_keys` fails the step `bad_params` and writes nothing. Outputs: `added` and
`already_present`.

Every setting may come from `config` or, per run, from an input of the same name.

## Normalisation

`normalise` is a mapping of switches, all off unless set. Nothing is built in.

| Switch | Effect |
|--------|--------|
| `lower_host` | lowercase the host |
| `drop_scheme` | drop `http:`/`https:` |
| `drop_fragment` | drop `#...` |
| `drop_trailing_slash` | drop a trailing `/` from the path |
| `drop_query` | a list of fnmatch patterns; matching query parameters are dropped |

With `lower_host`, `drop_scheme`, `drop_fragment`, `drop_trailing_slash` and `drop_query:
["utm_*", "ref"]`, `http://Example.com/a/?utm_source=x#f` and `https://example.com/a`
are the same key.

## Where the steps go

- Put `keyset.filter` where the items arrive, before anything uses them.
- Put `keyset.add` where a re-run would redo it if anything after it fails: after the
  flow's work and before its own commit step. Then a failed or skipped run never marks keys
  it did not deliver, and a crash between the add and the commit loses nothing (the add is
  idempotent).

## Admin verbs

Admin only, reads included (keys may be private URLs):

```bash
culture-rules keysets list                     # sets with their live key counts
culture-rules keysets show <set>               # keys with first_seen and expires_at
culture-rules keysets clear <set> [--key K]    # dry run: how many live keys would go
culture-rules keysets clear <set> --apply      # forget the set (or one key with --key)
```

The same verbs are MCP tools (`keysets_list`, `keysets_show`, `keysets_clear`) and API
routes (`GET /keysets`, `GET /keysets/{name}`, `POST /keysets/{name}/clear`).

## Worked example (not applied)

A daily digest that should not repeat a link it showed in the last 14 days. This is an
example of configuration set live through the CLI; nothing here ships enabled.

- In the search loop, between the search step and the section filter, a code step
  `config: {builtin: keyset.filter, set: "digest-links", key_field: "url", window_days: 14,
  normalise: {lower_host: true, drop_scheme: true, drop_fragment: true,
  drop_trailing_slash: true, drop_query: ["utm_*", "ref"]}}`, its `items` from the search
  results.
- After the publish steps and just before the step that records the issue delivered, a code
  step with the same settings and `builtin: keyset.add`, `max_keys: 200`, its `items` from
  the loop's results.
- Raise the search count so the filtered results leave enough fresh ones; a section left
  empty renders as having no results rather than repeating.
- Seed the set once from earlier runs with a one-off workflow: `run.read` of each past run's
  search results, then `keyset.add`.
