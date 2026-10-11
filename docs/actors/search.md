# Search app actor

A search app actor queries the [Brave Search API](https://brave.com/search/api/)
with the `search.web` action. It receives nothing: there is no webhook, so no
public path and no Cloudflare change.

This assumes you have a Brave Search API subscription, and that nothing else
is prepared. Read [README.md](README.md) first for the prerequisites and how
secrets work.

The secret this guide creates:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `BRAVE_API_KEY` | the Brave Search API subscription token | engine node |

## 1. Create the key and seal it

1. In the Brave Search API dashboard, open **API Keys** and create a key for
   your web search subscription.
2. On the host whose node runs the actor's actions:

   ```bash
   grant set BRAVE_API_KEY - --hidden     # paste the key, then Enter and Ctrl-D
   ```

## 2. Give the node the secret

The key is read when a search runs, on the machine named in the actor's
`machine`. Inject it into `culture-rules-node` (the README shows how):

```text
--inject CULTURE_RULES_SECRET_BRAVE_API_KEY=BRAVE_API_KEY
```

```bash
systemctl --user restart culture-rules-node
```

## 3. Create the actor

In the editor's **Actors** tab, add an **App** actor with surface
**Search**. Its only connection field is the key reference. With the CLI:

```json
{
  "id": "search-app",
  "name": "Web search (Brave)",
  "kind": "app",
  "machine": "<node host>",
  "params": {
    "surface": "search",
    "events": [],
    "actions": ["search.web"],
    "connection": {"api_key": "grant:BRAVE_API_KEY"}
  }
}
```

`connection.api_key` is required and must be a `grant:<NAME>` reference; a
literal is refused on save with `secret_literal`.

## 4. Use it in a rule

The `search.web` action takes:

| Param | Required | Meaning |
|---|---|---|
| `actor` | yes | the search actor's id |
| `query` | yes | the query, at most 400 characters (Brave's limit) |
| `count` | no | results wanted, at least 1; default 10, capped at 20 |
| `freshness` | no | `pd`, `pw`, `pm` or `py` (past day, week, month or year); default any age |
| `country` | no | a two-letter country code, or `ALL` |
| `lang` | no | the search language, such as `en` or `pt-br` |

The parameters are checked before the key is read. The call goes only to
Brave: the URL is a constant in `culture_rules/apps/brave.py`, with no
base-URL override.

The output is `{results, query, count}`. Each result is
`{title, url, snippet, age, source}`:

- tags are stripped from the title and snippet;
- `source` is the site's name, else its host;
- each field is cut to a fixed length;
- a result whose URL is not `http` or `https` is dropped.

At most `count` results come back, and `count` in the output is how many
did. A search is a read, so a retry is harmless.

## Failures

| Code | Meaning | Retried |
|---|---|---|
| `actor_not_found` | no enabled `search` actor with that id | no |
| `bad_query`, `bad_count`, `bad_freshness`, `bad_country`, `bad_lang` | a parameter is out of range | no |
| `secret_unavailable` | the key could not be resolved on the node (see step 2) | no |
| `http_<status>` | Brave refused the search; 429 and 5xx are retried | depends |
| `network_error`, `bad_response` | Brave was unreachable or answered with something other than JSON | yes |

## In the newspaper bundle

`docs/rules/newspaper/actors/newspaper-search.json` is a search actor on
machine `spark` with `grant:BRAVE_API_KEY`. The newspaper runs one
`search.web` per section, with the section's queries joined with `OR`; see
[the newspaper setup](../operations/newspaper.md).
