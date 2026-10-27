# HTTP policy on any actor (`http.call`)

An `http.call` action makes one HTTP request through the actor named in its
`params.actor`. Any actor can carry the policy: a service, a daemon, a
runner or an app. The policy sits in the actor's `params.http`, and with no
policy every call is refused. Nothing about any destination is built into
culture-rules.

Read [README.md](README.md) first for the prerequisites and how secrets
work.

## What the policy holds

```json
{
  "http": {
    "allow": ["127.0.0.1"],
    "headers": {"Authorization": "Bearer grant:EXAMPLE_API_KEY"},
    "save_roots": ["/srv/culture-rules/fetched"],
    "save_max_bytes": 52428800
  }
}
```

| Param | Required | Meaning |
|---|---|---|
| `allow` | yes | exact hostnames and IP literals a call may reach (no wildcards, any port). A hostname that resolves into a private range is still refused: allow the IP too |
| `headers` | no | sent on every call by this actor; they win over the action's own headers. Credentials here are references, never values |
| `save_roots` | no | absolute directories a call's `save_to` may name. Without it, `save_to` is refused |
| `save_max_bytes` | no | the largest body a `save_to` call keeps, a positive integer. Default 50 MiB (52428800) |

The Actors page has the same fields under "HTTP policy": allowed hosts, the
header rows, "Save roots" (comma separated) and "Largest saved body (bytes)".

## Header values that are secrets

A header value is a reference to a grant secret, resolved on the node at call
time. Two shapes are resolved:

- `grant:<NAME>`: the whole value is the secret;
- `<word> grant:<NAME>`: exactly one word of `[A-Za-z][A-Za-z0-9._-]*`, one
  space, then the reference, for example `Bearer grant:EXAMPLE_API_KEY`. The
  word is kept and the secret follows it.

A value holding `grant:` in any other shape (two words before it, two
references) is sent unchanged, never half-resolved. A resolved secret is
registered with the redactor and scrubbed from the step's output. The editor
accepts both shapes in a credential header and refuses a literal value, and
saving an actor accepts both under `headers` too.

Both shapes are resolved in the step's own `headers` as well as the policy's,
as a bare `grant:<NAME>` already was before the word form: any workflow author
can name a secret the node holds, and it is sent only to a host on the actor's
`allow` list. Keep that list as narrow as the secrets the node holds.

Seal the secret in grant and have the service unit inject it, as the README
describes under "How secrets work here":

```bash
grant set EXAMPLE_API_KEY - --hidden     # paste the value, then Enter and Ctrl-D
```

## Saving a response to a file (`save_to`)

An `http.call` step may set `save_to` to an absolute directory. It must equal
one of the actor's `save_roots` (a directory under a root is not accepted). The
body is then written to a file instead of coming back in the run:

- the file is `<root>/<sha256>.<ext>`, named by the body's SHA-256. The
  extension comes from the response `Content-Type`, never from the URL:
  `application/epub+zip` is `epub`, `application/pdf` is `pdf`,
  `text/markdown` is `md`, `application/json` is `json` and `text/plain` is
  `txt`. Any other type fails `unsupported_type` and writes nothing;
- the body is streamed in pieces under `save_max_bytes` and renamed into place
  atomically. A body over the cap fails `too_large` and leaves no file. A
  4xx or 5xx answer is never saved;
- the same bytes again reuse the file. A symlink (or anything that is not a
  regular file) already at that name is refused `path_refused`, and never
  written through;
- the root must be an existing directory with no symlink in its path.

The outputs are `status`, `path`, `sha256`, `size` and `content_type`. The body,
`truncated` and the response headers are not outputs, so file bytes never enter
run state. A later step reads the file by `path` under the same root, for
example `document.sections` with `content_path` and `content_root`.

`save_to` failing `path_refused` (a root the actor does not list, a symlinked
root) is final, and so are `unsupported_type` and `too_large`. A write error
fails `write_failed` and is retried.

## Setup

1. Create the actor (here a `service` actor) with the policy above, through the
   Actors page or `culture-rules actors create`.
2. Seal the secrets the headers name and inject them into the engine node's
   unit (README, "How secrets work here").
3. Make each `save_roots` directory on the node's machine, owned by the user
   the node runs as.
4. Use the actor in an `http.call` step: `actor`, `method`, `url`, and `save_to`
   when the body should be a file.

A worked example that uses all of it, with the sections step, is
[the sources manifest](../operations/sources-manifest.md).
