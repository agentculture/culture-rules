# Email app actor

An email app actor sends mail through [Resend](https://resend.com) with the
`email.send` action. It receives nothing: there is no webhook, so no public
path and no Cloudflare change.

This assumes you have a Resend account whose sending domains are verified,
and that nothing else is prepared. Read [README.md](README.md) first for the
prerequisites and how secrets work.

The secret this guide creates:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `RESEND_API_KEY` | a Resend API key with sending access | engine node |

## What the actor may send

The actor's connection fixes who the mail is from and who it may go to. A
step cannot change either.

| Field | Meaning |
|---|---|
| `api_key` | `grant:<NAME>`, required. A literal is refused on save with `secret_literal` |
| `domain` | the sender domain: `culture.dev` or `jetson-ai-lab.org` (`EMAIL_DOMAINS` in `culture_rules/model/app_actor.py`), default `culture.dev`. Any other domain is refused on save |
| `local_part` | the part before `@`, default `no-reply`. The from address is `local_part@domain` |
| `recipients` | the allowlist of addresses, required. It may be empty, and an empty list refuses every recipient |

The API checks these fields on every save and names the bad one, such as
`connection.domain` or `connection.recipients[0]`.

## 1. Verify the domain in Resend

In Resend, open **Domains** and check that the sender domain is **Verified**.
Both `culture.dev` and `jetson-ai-lab.org` can sit under one account and one
key.

## 2. Create the API key and seal it

1. In Resend, open **API Keys**, **Create API Key**. Give it **Sending
   access**, optionally limited to the sender domain. The key is shown only
   once.
2. On the host whose node runs the actor's actions:

   ```bash
   grant set RESEND_API_KEY - --hidden     # paste the key, then Enter and Ctrl-D
   ```

## 3. Give the node the secret

The key is read when a send runs, on the machine named in the actor's
`machine`. A hidden secret is readable there only when grant injects it into
the node, so add this to `culture-rules-node` (the README shows how):

```text
--inject CULTURE_RULES_SECRET_RESEND_API_KEY=RESEND_API_KEY
```

```bash
systemctl --user restart culture-rules-node
```

## 4. Create the actor

In the editor's **Actors** tab, add an **App** actor with surface **Email**.
The sender domain is a drop-down with the two allowed domains. Or write the
JSON and create it with the CLI:

```json
{
  "id": "email-app",
  "name": "Email (Resend)",
  "kind": "app",
  "machine": "<node host>",
  "params": {
    "surface": "email",
    "events": [],
    "actions": ["email.send"],
    "connection": {
      "api_key": "grant:RESEND_API_KEY",
      "domain": "culture.dev",
      "local_part": "no-reply",
      "recipients": ["<recipient address>"]
    }
  }
}
```

Keep `recipients` to the people the actor exists for. Add an address later
by editing the actor; the allowlist is the only thing that lets mail out.

## 5. Use it in a rule

The `email.send` action takes:

| Param | Required | Meaning |
|---|---|---|
| `actor` | yes | the email actor's id |
| `to` | yes | a list of addresses, each on the actor's allowlist (compared case-insensitively) |
| `subject` | yes | the subject line |
| `html`, `text` | at least one | the body as HTML, as plain text, or both |
| `idempotency_key` | no | replaces the engine's key: a string of at most 256 characters |
| `sender` | no | replaces the actor's from address; must be an address at the actor's `domain` |

Attachments are not supported. The output is `{message_id}`, Resend's id for
the message.

The engine's idempotency key for the step goes out as Resend's
`Idempotency-Key`, so a retried send sends one email. That key is per run.
To make several runs share one, pass `idempotency_key`: the daily newspaper
passes `newspaper:<date>:email`, so a later run for the same date cannot send
it again. Resend keeps a key for 24 hours, and the same key with another body
is refused (`http_409`). So a resend must repeat the whole request: the
newspaper freezes the recipients and the sender with the first request for
the date and passes them as `to` and `sender`.

## Failures

| Code | Meaning | Retried |
|---|---|---|
| `actor_not_found` | no enabled `email` actor with that id | no |
| `bad_input` | `to` is not a non-empty list, the subject or body is missing, or `sender` is not an address | no |
| `sender_not_allowed` | `sender` is not at the actor's `domain`. Checked before the key is read | no |
| `recipient_not_allowed` | an address in `to` is not on the allowlist. Checked before the key is read; the refusal names no secret | no |
| `secret_unavailable` | the key could not be resolved on the node (see step 3) | no |
| `http_<status>` | Resend refused the send; 429 and 5xx are retried | depends |
| `network_error`, `bad_response` | Resend was unreachable or answered without an id | yes |

## In the newspaper bundle

`docs/rules/newspaper/actors/newspaper-email.json` is an email actor on
machine `spark`. It sends as `newsletter-no-reply@culture.dev` with
`grant:RESEND_API_KEY`, and its allowlist ships **empty**. Add the reader's
address before enabling the newspaper rule; see
[the newspaper setup](../operations/newspaper.md).
