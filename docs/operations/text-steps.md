# Text steps

Three generic builtin code steps work on text: `text.number`,
`text.replace_refs` and `text.validate`. None of them holds a format, a
reference syntax or a rule of its own. Each one does only what its step
config says, so how a flow numbers, cites or checks text is configuration,
not code. Each input may instead sit in the step's `config` (the input wins),
and a loop body can map `inputs.item.*` onto them with `config.with`.

## `text.number`

Joins a list of objects into one text, each item rendered through a template
and numbered from 1 in list order.

| Input | Required | Meaning |
|---|---|---|
| `items` | yes | a list of objects |
| `template` | yes | text with `{n}` (the 1-based position) and `{field}` (a top-level scalar field of the item); `{{` and `}}` are literal braces |
| `separator` | yes | the text between rendered items (`""` is allowed) |

Outputs: `text` and `count`. Any other brace form (`{}`, `{0}`, `{a.b}`,
`{a!r}`, a stray brace) fails `bad_params`, as does an item that is not an
object or a field the item lacks or holds as a list, object or null. `n` is
always the position, never an item field.

## `text.replace_refs`

Rewrites the numbered references in a text, such as `[2]`, with a template
filled from the item at that number.

| Input | Required | Meaning |
|---|---|---|
| `text` | yes | the text to rewrite |
| `items` | yes | a list of objects, numbered from 1 |
| `pattern` | yes | a regular expression with exactly one group holding the number, such as `\[(\d+)\]`; it must pass the engine's regex safety check |
| `template` | yes | the replacement: `{n}` and `{field}` of item n, with the same brace rules as `text.number` |

Outputs: `text` (rewritten), `replaced` (how many references were replaced)
and `missing_refs` (the sorted numbers left as they were). A reference
outside 1 to the number of items, or one whose item lacks a field the
template names, is a data gap: it stays in the text, is listed, and the step
still succeeds. A group that is not all digits is left alone.

## `text.validate`

Checks a text against explicit rules and answers with a verdict. A refusal is
a verdict, not a failure: the step succeeds with `ok: false` and `reasons`,
so a later step can branch on it with `when`. No rule is on by default; with
none given, the text is ok.

| Input | Meaning |
|---|---|
| `text` | the text (required) |
| `allowed`, `allowed_field` | a list of objects and the field holding the only URLs a Markdown link (`[label](url)`) may point to; give both or neither. A label may hold one balanced level of brackets, so `[[1]](url)` is a link whose text is `[1]` |
| `require_links` | at least one link |
| `line_shape` | `bullets` (every non-blank line is a bullet) or `linked_bullets` (a bullet that also holds a link) |
| `link_schemes` | the schemes a usable URL may have; `http` and `https` when unset |

Outputs: `ok`, `reasons` (in rule order: `no_links`, `link_not_allowed`,
`not_bullets`, `bullet_without_link`), `text` (unchanged) and `links` (every
link URL, in order).

## Example: a cited summary

A summary that cites its sources by number, then shows each citation as a
link. The prompt, the formats and the rule are all step config.

1. `text.number` turns the sources into the model's input, with
   `template: "[{n}] {title}: {snippet}"` and `separator: "\n"`.
2. `llm.complete` names a brief (in its actor's `params.briefs`) that asks the
   model to cite each claim with the `[n]` of its source, and gets that text as
   its `content`.
3. `text.replace_refs` turns each `[n]` into a link, with
   `pattern: "\\[(\\d+)\\]"` and `template: "[[{n}]]({url})"`, so the link
   text is `[1]` (wider to tap than `1`).
4. `text.validate`, with `allowed` the sources, `allowed_field: "url"` and
   `require_links: true`, checks that every link is one of the sources; a step
   after it can fall back to the plain list when `ok` is false.
