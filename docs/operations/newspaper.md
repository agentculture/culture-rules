# The daily newspaper (#53)

Every morning at 06:30 Asia/Jerusalem, culture-rules searches the web for
the sections you choose and has Qwen (cortex) summarise what it found. It
then delivers one dated issue in two ways: by email from the
`newsletter-no-reply` sender on `culture.dev`, and as an EPUB queued on your
Xteink reader. The issue's date is the local date of the 06:30 slot, and the
schedule follows DST.

The newspaper is committed data in `docs/rules/newspaper/`, in the import
format, and it ships **disabled**:

| File | What it is |
|---|---|
| `actors/newspaper-email.json` | app actor, surface `email`: Resend, sender `newsletter-no-reply` on `culture.dev`, **empty** recipient allowlist |
| `actors/newspaper-search.json` | app actor, surface `search`: Brave web search |
| `actors/newspaper-llm.json` | agent actor: the OpenAI-compatible lobes gateway, model `cortex-spark2`, pool `qwen-spark2`, `max_concurrency: 1` |
| `actors/newspaper-library.json` | service actor: the xteink library API |
| `workflows/daily-newspaper.json` | the workflow (below) |
| `rules/daily-newspaper.json` | schedule `30 6 * * *`, tz `Asia/Jerusalem`, placed on spark, disabled |
| `variables.json` | the three variables with example values and descriptions |
| `seed-variables.sh` | seeds the variables from `variables.json` (dry run by default) |

No key, address, device id or non-loopback URL is committed. The keys are
grant references, the base URLs are spark's loopback, and the recipient and
device are values you seed.

## What a run does

The workflow `daily-newspaper` has no bridge actor and no agent step. Every
step is a built-in or an action:

1. `sections` (`newspaper.sections`) parses `newspaper_sections`. A bad
   value fails the run here, naming the field, before any claim or search.
2. `claim` (`newspaper.issue`, phase `claim`) takes the slot's local date.
   For a date already delivered, or one another run holds, it answers
   `deliver: false` and every later step is skipped. The run then succeeds
   with nothing searched or sent. It also answers `summaries_until`: the
   slot plus the claim step's `config.summary_window_s` (600 seconds, so
   06:40).
3. `search` is a `for_each` over the sections. For each one, `search_web`
   (`search.web`) sends the section's queries joined with `OR`, with its
   `count` and `freshness`, and `filter` (`newspaper.filter`) applies its
   must and must-not terms and site lists, then dedupes and caps the results.
   A result whose URL does not parse is dropped; the others are kept.
4. `summarise` is a `for_each` over the filtered results. `summary`
   (`llm.complete` on `newspaper-llm`) writes one bullet per result, each
   linking its source. The brief is locked in code and the request sends no
   tools. Its `not_after` is `summaries_until`: no summary starts after it.
5. `render` (`newspaper.render`) builds the HTML email body and the EPUB.
   A summary is shown only when every line is a bullet that links one of
   its section's results; otherwise the section shows its results. A
   section with no results says "No results."
6. `email_gate` (`newspaper.issue`, phase `before_email`) fences the email.
   It renews the run's lease on the date. On the date's first fence it
   freezes the whole request: the recipients (`newspaper_to`), the sender
   of `newspaper-email` (the step's `config.email_actor`), the subject and
   the HTML. It answers that stored request with the key
   `newspaper:<date>:email` and `send_until`, the date's first attempt plus
   23 hours. It answers `send: false` when the date's email is already
   recorded sent, or is uncertain (see below).
7. `email` (`email.send`) sends the frozen request, with that key as
   Resend's `Idempotency-Key`. The recipients are still checked against
   the actor's current allowlist. Its `not_after` is `send_until`, checked
   before every attempt. The step has `config.on_error: "skip"`, so a
   failure goes on to `push_gate`, which decides.
8. `push_gate` (`newspaper.issue`, phase `before_push`) records the email's
   message id for the date, then renews the lease. With no message id it
   fails the run, unless the first attempt is past its 23 hours: then the
   email is recorded uncertain and the push goes ahead.
9. `push` (`library.push`) uploads the EPUB to xteink (kind `article`, title
   `Daily newspaper <date>`) and queues it on the device.
10. `record` (`newspaper.issue`, phase `record`) marks the date delivered
    with the message id and the item and queue ids.

The run's outputs are `message_id`, `item_id`, `date` and
`email_uncertain`. They are shown on
the run, and the Statistics lane shows the run.

**When cortex is down.** The `summary` step has `config.on_error: "skip"`.
The engine turns its failure into `skipped`, keeps the error on the step,
clears its outputs and records `failure_tolerated`. Its failures include an
unreachable gateway after its two attempts and the summary window closing
(`llm_deadline`). The issue still goes out on time, with that section's
results and the line "Summary unavailable." When no section got a summary,
the masthead also says "Summaries were unavailable for this issue." once.
Search and library failures
are not tolerated: they fail the run. The `email` step also has
`on_error: "skip"`, but only so `push_gate` can tell a passed window from
a real failure. A failed email inside the window still fails the run there.

**Waiting for the pool.** The summaries share the `qwen-spark2` pool with
the PR fixer, so each summary waits behind a running fix, but only until
`summaries_until`. Past it, a queued summary gives up at its next re-ask
(within a minute), and no later summary starts. On a busy morning the issue
goes out by 06:45 with the results only. To widen or narrow the window,
change `summary_window_s` on the workflow's `claim` step.

**One issue per date.** Each date has one document in the
`newspaper_issues` collection. The run that claims a date holds a one-hour
lease on it, which each fence renews. Another run takes the date over only
after the lease runs out, and a run whose date was taken over fails its
next fence with `claim_lost` and sends nothing more. Delivery progress is
kept per destination: `email` (`message_id`, `at`) as soon as the email is
known sent, and `library` (`item_id`, `queue_id`, `at`) with the delivery
record. So if the email went out but the push failed, the next run for that
date skips the email and retries only the push. The EPUB has no
idempotency key: a crash between a push and its record can queue the issue
on the reader twice.

**Email delivery guarantees.** A date is never emailed twice. A missed
email is preferred to a duplicate:

- The first `email_gate` that answers `send: true` records
  `email_attempt` (`at`, `key`) on the date. Later fences never move it.
- If Resend accepted the email but its answer was lost, the email is not
  recorded. A run within 23 hours of the first attempt resends the stored
  request under the same key. The request is identical, recipients and
  sender included, even if `newspaper_to` or the sender changed since.
  Resend keeps a key for 24 hours, so it answers the first message id and
  sends nothing.
- An email whose acknowledgement was lost and not retried within 23 hours
  is not resent. Resend may have forgotten the key. The fence records
  `email: {uncertain: true, attempted_at}` and answers `send: false` and
  `email_uncertain: true`. The push still goes ahead, and the run succeeds
  with `email_uncertain: true` and no `message_id`. Check the inbox (or
  Resend's log) for that date.
- The attempt is recorded before the send. An `email` step that failed
  before it reached Resend also ends uncertain once 23 hours have passed.
- The 23 hours bound every send, not just the fence. A run whose `email`
  step was waiting to retry when the node went down, and resumes a day
  later, retries with `not_after` already past. The step fails
  `email_window_passed` without a request, `push_gate` records the email
  uncertain, and the run succeeds with `email_uncertain: true`.
- Any other email failure (`recipient_not_allowed`, an `http_4xx`, the
  retries used up) is not passed as delivered. `push_gate` sees no message
  id inside the window and fails the run. The `email` step keeps its error.
- A frozen recipient that was since removed from the allowlist is refused
  with `recipient_not_allowed` (not retried). It is never swapped for the
  new `newspaper_to`.

## 1. Grants (on spark)

The four actors live on machine `spark`. Their actions run in spark's
`culture-rules-node` and resolve each `grant:` reference at call time, first
from an injected `CULTURE_RULES_SECRET_<NAME>` variable and otherwise with
`grant get`, which refuses a hidden secret:

| grant name | Used by | Value |
|---|---|---|
| `RESEND_API_KEY` | `newspaper-email` | the Resend API key (both sender domains are verified on it) |
| `BRAVE_API_KEY` | `newspaper-search` | the Brave Search API subscription token |
| `LOBES_GATEWAY_API_KEY` | `newspaper-llm` | the lobes gateway's bearer key |
| `XTEINK_PUSH_API_KEY` | `newspaper-library` | an xteink `xtk_` key |

Store each one hidden, with the value read from stdin:

```bash
grant set XTEINK_PUSH_API_KEY - --hidden
```

**Inject the keys into spark's node.** `RESEND_API_KEY` and `BRAVE_API_KEY`
are hidden, so the node must start with them injected (inject all four, for
uniformity). Spark's node unit is hand-edited: open the user unit
`culture-rules-node.service` with `systemctl --user edit --full culture-rules-node`. Add these to its
`ExecStart`, after the existing `grant run --inject ...` options and before `--`:

```text
--inject CULTURE_RULES_SECRET_RESEND_API_KEY=RESEND_API_KEY
--inject CULTURE_RULES_SECRET_BRAVE_API_KEY=BRAVE_API_KEY
--inject CULTURE_RULES_SECRET_LOBES_GATEWAY_API_KEY=LOBES_GATEWAY_API_KEY
--inject CULTURE_RULES_SECRET_XTEINK_PUSH_API_KEY=XTEINK_PUSH_API_KEY
```

Then run `systemctl --user daemon-reload` and restart `culture-rules-node`
(pause the engine first if a run is active). Without the injection, a send
fails `secret_unavailable`.

**The `xtk_` key.** Any xteink key is a full operator key. A key used only
for the newspaper can be revoked on its own. To use one, mint it on the
xteink host with `create-key newspaper` and store it under the same grant
name.

## 2. Actor addresses and placement

Both base URLs are spark-local services:

- `newspaper-llm`: `params.base_url` is `http://127.0.0.1:8001`, the lobes
  gateway. `params.model` must stay `cortex-spark2`: the gateway's first
  model is a small one, and the port never relies on a default.
- `newspaper-library`: `params.base_url` is `http://127.0.0.1:8780`, xteink.

An action step runs where its actor lives (`machine: spark`), so these
loopback addresses are right as shipped. If you move an actor to another
machine, set its `base_url` to the service's LAN or tailnet address. Store
that address only in the database (`actors update`), never in the
committed JSON: `scripts/scan-secrets.py` rejects a non-localhost URL there.

## 3. Variables

| Variable | Value | Shipped |
|---|---|---|
| `newspaper_sections` | one JSON **string**: the list of sections | a generic two-section example |
| `newspaper_to` | a list of recipient addresses | `[]`: the email step fails and nothing is sent |
| `newspaper_device` | the Xteink device id (`GET /api/devices` on xteink) | `null`: the library actor's `device_id`, if you set one there |

Each section is an object. Its keys:

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | the section heading, at most 80 characters |
| `queries` | yes | 1 to 5 search queries; joined with `OR` into one Brave query of at most 400 characters |
| `must` / `must_not` | no | terms a result's title or snippet must contain or must not contain (case-insensitive) |
| `freshness` | no | `pd`, `pw`, `pm` or `py` (Brave's day, week, month or year); default any age |
| `count` | no | results kept, 1 to 20 (default 5) |
| `site_include` / `site_exclude` | no | hostnames; a hostname also matches its subdomains |

Any other key is refused. An example value:

```json
[
  {"name": "Edge AI", "queries": ["edge AI hardware news"], "freshness": "pd", "count": 5},
  {"name": "Open models", "queries": ["open source LLM release"], "freshness": "pw",
   "must_not": ["sponsored"]}
]
```

Seed the shipped values, then set the real ones:

```bash
bash docs/rules/newspaper/seed-variables.sh            # dry run
bash docs/rules/newspaper/seed-variables.sh --apply
# sections.json holds the list; --value takes it as one JSON string
python3 -c 'import json, sys; print(json.dumps(open(sys.argv[1]).read()))' \
  sections.json > sections-value.json
culture-rules variables set newspaper_sections --value @sections-value.json --apply
culture-rules variables add newspaper_to <your address> --apply
culture-rules variables set newspaper_device --value <device id> --apply
```

`newspaper_sections` holds a JSON *string*, not a list, which is why the
value is quoted first. You can also edit it in the Variables tab. A change
takes effect at the next issue, with no code or bundle change.

## 4. Import, then allow the recipient

Import in this order: actors, workflows, rules. Each import is a dry run
without `--apply`:

```bash
culture-rules actors import docs/rules/newspaper --apply
culture-rules workflows import docs/rules/newspaper --apply
culture-rules rules import docs/rules/newspaper --apply
```

Then add your address to the email actor's allowlist. The shipped list is
empty, and `email.send` refuses any recipient outside it before it reads
the key. Export the actor, add the address to
`params.connection.recipients`, and write the actor back:

```bash
culture-rules actors show newspaper-email --json > email-actor.json
# edit params.connection.recipients: ["<your address>"]
culture-rules actors update newspaper-email --body @email-actor.json --apply
```

You can also do this in the editor's Actors tab. `newspaper_to` and the
allowlist must both name the address.

## 5. Enable

```bash
culture-rules rules enable daily-newspaper --apply
```

The rule is placed on spark, and its slot fires once at 06:30 local time.
A day missed because spark's node was not running is skipped, never caught
up.

## 6. Check the first run

After 06:30, look for the run, its outputs and the delivery record:

```bash
culture-rules runs list --rule-id daily-newspaper --limit 3
culture-rules runs show <run id> --json
```

A good run has `status: succeeded` and outputs `message_id`, `item_id`,
`date` and `email_uncertain: false`. Its `summary` steps succeeded, or show `skipped` with an
`llm_unavailable`, `llm_deadline` or `queue_timeout` error. The delivery is
recorded in the `newspaper_issues` collection, one document per date with
state `delivered`. The issue is in the inbox and queued on the reader.

A run that ends quickly with `claim` answering `already_delivered` or
`claimed_by_another_run` is the per-date guard at work. A run that failed
at `sections` names the bad field of `newspaper_sections`. A failure at
`email` with `bad_input` or a refused recipient means `newspaper_to` or the
allowlist is not set.

A run that failed at `email_gate` or `push_gate` with `claim_lost` was
taken over by a later run for the same date, which delivers instead.

If a run fails after `claim`, its lease keeps that date for an hour from
its last fence. A manual re-run for the same date within that hour sends
nothing. After the hour, a re-run takes the date over: it skips an email
that was already sent and retries the push. Its slot is in the past, so its
summary window has closed and it sends the results only. A re-run more than
23 hours after an unacknowledged email sends no email and reports
`email_uncertain: true` (see the email delivery guarantees above).

## Known limits

- **The summary replay ledger is per process.** `llm.complete` replays a
  completed summary for the same step from memory (its last 1024
  completions), so a summary whose acknowledgement was lost is not asked
  again. The memory does not survive a node restart. A summary that
  completed just before spark's node restarted, and is re-asked after its
  `not_after`, fails `llm_deadline`: that section goes out with its results
  only. It is a missed summary, never a wrong send.
