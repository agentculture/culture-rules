# GitHub app actor

A dedicated GitHub App receives repository events (PRs, issues, comments,
reviews) and comments on issues and PRs as itself.

This assumes you are signed in to GitHub as an owner of the organisation, and
that nothing else is prepared. Read [README.md](README.md) first for the
prerequisites and how secrets work.

The secrets this guide creates:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `RULES_GITHUB_WEBHOOK_SECRET` | random value GitHub signs each delivery with | API |
| `RULES_GITHUB_APP_PRIVATE_KEY` | the App's private key (`.pem`) | engine node |

## 1. Create the webhook secret

On the serving host:

```bash
grant generate RULES_GITHUB_WEBHOOK_SECRET --hidden --bytes 32 --encoding hex
grant run --inject V=RULES_GITHUB_WEBHOOK_SECRET -- sh -c 'printf "%s\n" "$V"'
```

Keep the printed value for step 2, then clear your terminal.

## 2. Create the GitHub App

1. Open the organisation, then **Settings**, **Developer settings**, **GitHub
   Apps**, **New GitHub App**.
2. Fill in the form:

   | Field | Value |
   |---|---|
   | GitHub App name | for example `rules-culture-dev` (it becomes the bot login `<slug>[bot]`) |
   | Homepage URL | the editor's address, for example `https://rules.culture.dev` |
   | Webhook: Active | ticked |
   | Webhook URL | `https://<rules host>/hooks/github` |
   | Webhook secret | the value printed in step 1 |
   | Repository permissions | Issues: Read and write; Pull requests: Read and write; Metadata: Read; Contents: Read and write; Checks: Read; Actions: Read and write (the fixer only reads; write is kept on purpose for future use). Never Workflows |
   | Subscribe to events | Pull request, Issue comment, Issues, Pull request review, Pull request review comment, Check suite, Workflow run |
   | Where can this App be installed | Only on this account |

   Contents write is for `github.push`, which mints a fresh token per push
   scoped to the one repository and to `contents: write` only. Checks and
   Actions read let the fixer see check suites and workflow runs finish and
   read failing job logs. Changing permissions on an existing App only takes
   effect after the installation owner accepts the update under the org's
   **Settings → GitHub Apps**.

   **Do not skip the webhook secret.** Without it GitHub sends unsigned
   deliveries, and every one is refused with 401.
3. Click **Create GitHub App**. Note the **App ID** at the top of the page.
4. Under **Private keys**, click **Generate a private key**. A `.pem` file
   downloads.

## 3. Seal the private key

Copy the `.pem` to the serving host, then:

```bash
grant set RULES_GITHUB_APP_PRIVATE_KEY - --hidden < rules-culture-dev.private-key.pem
shred -u rules-culture-dev.private-key.pem
```

Also delete the downloaded copy on your own machine.

## 4. Install the App

On the App's page, open **Install App**, then **Install** next to the
organisation. Choose **All repositories**, or select the ones rules may act on.
After installing, the browser shows `.../settings/installations/<number>`. That
number is the **installation ID**.

## 5. Open the webhook path in Cloudflare Access

GitHub cannot sign in through Access. Give exactly this one path its own bypass,
which the receiver protects with the signature check:

1. In Cloudflare **Zero Trust**, open **Access**, **Applications**, **Add an
   application**, **Self-hosted**.
2. Fill in:
   - **Application name:** `<rules host> webhook github`.
   - **Domain:** `<rules host>`.
   - **Path:** `hooks/github`.
3. Add a policy: **Action: Bypass**, **Include: Everyone**. Save.

Never use `hooks/*` or an empty path. Check it from any machine:

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST https://<rules host>/hooks/github -d '{}'
```

This should print `401` (our receiver). `302` means the bypass is not in
effect.

## 6. Give the services the secrets

The API verifies each delivery with the webhook secret. The node signs the
App's JWT with the private key when it comments. Add one injection to each unit
on the serving host. The README shows how (`systemctl --user edit --full ...`).

```text
# culture-rules-api
--inject CULTURE_RULES_SECRET_RULES_GITHUB_WEBHOOK_SECRET=RULES_GITHUB_WEBHOOK_SECRET
# culture-rules-node
--inject CULTURE_RULES_SECRET_RULES_GITHUB_APP_PRIVATE_KEY=RULES_GITHUB_APP_PRIVATE_KEY
```

Then restart both units:

```bash
systemctl --user restart culture-rules-api culture-rules-node
```

## 7. Create the actor

In the editor's **Actors** tab, add an **App** actor and set **Machine** to the
serving host, or write the JSON with the CLI:

```json
{
  "id": "github-app",
  "name": "GitHub (rules-culture-dev App)",
  "kind": "app",
  "machine": "<serving host>",
  "params": {
    "surface": "github",
    "events": ["github.pr.opened", "github.pr.closed", "github.pr.reopened",
               "github.comment.created", "github.issue.opened", "github.review.submitted"],
    "actions": ["github.comment"],
    "self_identity": "<app slug>[bot]",
    "connection": {
      "app_id": "<App ID>",
      "installation_id": "<installation ID>",
      "private_key": "grant:RULES_GITHUB_APP_PRIVATE_KEY",
      "webhook_secret": "grant:RULES_GITHUB_WEBHOOK_SECRET",
      "repos": ["<owner>/<repo>"]
    }
  }
}
```

`repos` is the allow-list for **comments**: the App may only comment on these
repositories. Events arrive from every repository the App is installed on.

## 8. Verify

1. Open a PR, or comment on one, in an installed repository.
2. On the App's page, open **Advanced**, **Recent Deliveries**. The new delivery
   shows `202`.
3. A rule with an **Event** trigger `github.pr.opened` and a `github.comment`
   action `{actor: "github-app", repo, number, body}` comments as
   `<app slug>[bot]`.

## Events and data

| GitHub event / action | Event type |
|---|---|
| `pull_request` opened / closed / reopened | `github.pr.opened` / `.closed` / `.reopened` |
| `pull_request` synchronize / ready_for_review | `github.pr.synchronize` / `github.pr.ready` |
| `issue_comment` created | `github.comment.created` |
| `issues` opened | `github.issue.opened` |
| `pull_request_review` submitted | `github.review.submitted` |
| `pull_request_review_comment` created | `github.review_comment.created` |
| `check_suite` completed | `github.checks.suite_completed` |
| `workflow_run` completed | `github.checks.workflow_completed` |
| (derived, once per head SHA) | `github.pr.checks_settled` |

Every event's `data` holds `repository`, `number`, `title`, `url`, `author`
and `action`, plus `delivery_id` and `actor`. With a `self_identity` it also
holds `self_authored`. Each type then adds:

| Event type | Extra fields |
|---|---|
| `github.pr.*` | `merged` and the PR facts |
| `github.review.submitted` | `review_state` and the PR facts |
| `github.review_comment.created` | the PR facts; `author` is the comment's author |
| `github.comment.created` on a PR | `comment` (truncated), `pr_enriched` and, when enriched, the PR facts |
| `github.comment.created` on an issue | `comment` (truncated) |
| `github.checks.*` | `head_sha`, `head_branch`, `pr_numbers`, `app_slug`, `workflow_name`, `status`, `conclusion` |
| `github.pr.checks_settled` | `head_sha`, `head_branch`, `pr_numbers`, `number`, `settled_by`, `conclusion`, `failed_apps` (d25: app slugs whose suites concluded `failure`) and the PR facts |
| `github.pr.checks_failed_late` | d25: a counted app's suite concluded `failure` after its head settled. The settled event's data, with `failed_apps` grown by the app, `late_app`, `conclusion: failure` and `settled_by: late`; once per (repo, head SHA, app) |

The **PR facts** have the same names on every PR-scoped type, so the fixer
rules share one condition shape and the workflow gets the same inputs
whichever rule fired:

| Field | From the pull request |
|---|---|
| `head_sha`, `head_branch`, `head_repo` | `head.sha`, `head.ref`, `head.repo.full_name` |
| `base_repo`, `base_branch`, `base_sha` | `base.repo.full_name`, `base.ref`, `base.sha` |
| `draft` | `draft` (a boolean) |
| `pr_author` | `user.login` |

Each fact is checked: repos must be `owner/name`, SHAs 40 hex digits,
branches and `pr_author` non-empty strings, and `draft` a real boolean. A
missing or malformed fact is **left out**, never stored as `null` or given a
default. A comparison on a missing field is false, so two missing repos never
satisfy `head_repo == base_repo`. A PR from a deleted fork (`head.repo` is
`null`) has no `head_repo`.

Where the facts come from:

- **PR, review and review-comment events** take them from the webhook
  payload.
- **A comment on a PR** has no head or base in its payload. The receiver reads
  the PR through the App, read-only (`GET /repos/{repo}/pulls/{n}`), within 5
  seconds. The repository must be in some enabled GitHub actor's
  `connection.repos`.
  - On success, `pr_enriched` is `true`, and all eight facts are present.
  - If there is no allow-listed actor, the read fails or times out, or the
    answer lacks any valid fact, the comment is still stored, without the
    facts and with `pr_enriched: false`. A condition on
    `head_repo == base_repo` and `draft == false` then does not match, so the
    fixer does not fire.
  - The 5 seconds cover the whole lookup, including resolving the App's
    private key. A slow first resolve finishes in the background and is
    cached, so later lookups are fast. At most two lookups run at once; one
    more fails at once and is stored unenriched.
  - A comment on a plain issue is never looked up and has no `pr_enriched`.
- **`github.pr.checks_settled`** reads its first PR the same way, best-effort.
  It adds the facts only when all of them are valid.
  `head_sha` stays the settled SHA and `head_branch` the check's own.

A settled event's `conclusion` counts only the suites of apps not in
`ignored_check_apps`:

| `conclusion` | When |
|---|---|
| `success` | every counted suite completed `success`, `neutral` or `skipped` |
| `failure` | every counted suite completed, at least one otherwise |
| `timeout` | the timeout passed with a counted suite still running |
| `no_checks` | the timeout passed with no counted suite at all |

No counted suite is never green. If every listed suite comes from an ignored
app, or none is listed yet, the SHA keeps waiting for one to appear. If none
does, it settles at the timeout as `no_checks`. Only `success` resets a
rule's attempt budget, so `no_checks` never does.

The receiver stores a check completion first and then arms its head SHA for
the settle. If arming fails (a store error, say), the webhook answers `503`.

After arming, the receiver may settle the SHA at once. Its GitHub reads, the
suite listing and the PR read, share one 5-second budget per delivery,
including resolving the App's private key. They run on the same two capped
lookup workers as the comment lookup. If the listing runs out of time, or the
PR read does, the receiver still answers quickly. The SHA stays armed, and the
node's settle tick emits the settled event with the PR facts.
GitHub does **not** redeliver a failed delivery on its own, so the node
recovers it instead:

- Every settle tick reads the stored `github.checks.suite_completed` and
  `github.checks.workflow_completed` events, oldest first by receipt time.
  It starts after a watermark that all nodes share (collection
  `checks_settle_recovery`).
- The scan is bounded. A tick reads at most 100 completions. It skips
  completions younger than 2 minutes, which covers an arm still in flight and
  clock skew between hosts. It never goes back more than 24 hours.
- A SHA that already has a settle record is left alone, whether that record
  is pending or settled by completion or by timeout. So is a SHA with a
  settled event.
- A SHA with neither is armed as of the completion's receipt time. The
  minimum window and the timeout run as if the webhook had armed it.
- The once-per-SHA insert, the poll claim and the deterministic event id keep
  concurrent nodes to one settled event.
- A store error stops the scan before the failed completion, and the next
  tick retries it.

Recovery only arms a SHA, which needs no credentials, so every node does it.
Polling the suites and emitting the settled event are restricted:

- A node claims a pending SHA's poll only if it can serve the repository's
  App actor. The actor must be placed on that node's machine, and its private
  key must resolve on that node.
- An actor with no `machine` is served by any node whose private key
  resolves. Set no machine only when you put the key on every node you want
  to share the settle.
- A node without the key never claims a poll, so it cannot time a SHA out
  without the PR facts. It retries a failed key resolve once a minute.

What recovery cannot cover:

- If storing the completion itself fails, nothing is recorded. Redeliver it
  by hand from the App's **Recent Deliveries**.
- A completion more than 24 hours old when the nodes reach it is never
  armed.

A `ping` answers `200 {"pong": true}`. A redelivery is deduplicated by
`X-GitHub-Delivery`. It is never looked up again, so it cannot change what
was stored.

## Reading and merging a PR

Two action kinds use the App beyond comments and pushes. Both take
`params.actor` (the GitHub app actor's id, written as a plain id), `repo` (in
the actor's `connection.repos`) and `number`.

### `github.pr` (read only)

Reads one PR as the App and outputs `title`, `url`, `author`, `draft`, `state`,
`merged`, `head_repo`, `base_repo` (both `owner/name`; `head_repo` different
from `base_repo` means a fork), `head_branch`, `head_sha`, `base_branch` and
`base_sha`. It writes nothing.

### `github.merge` (squash, fenced)

The one merge kind in culture-rules. It squash-merges a PR as the App, at the
commit a prompt was shown for. Params: `actor`, `repo`, `number`, `sha`
(required: the full 40-hex head commit) and `delete_branch` (optional boolean).
The method is always `squash`.

Merging stays a human gate, fenced by an operator's click and a trusted merge
role. The fence is checked first, from the store only, before any secret is
read or any request is made. A refused call fails the step without retry:

| Error | Meaning |
|---|---|
| `not_started_by_click` | The run was not its rule firing on a stored `discord.button.clicked` event. A direct run, a scheduled run, a run started through the API and a chained run are all refused |
| `workflow_not_trusted` | The run's pinned workflow is not trusted in the `merge` role (below) |
| `rule_disabled`, `workflow_disabled` | The run's rule or workflow is no longer enabled |
| `run_not_found` | The run is gone |

Which person may click is the click rule's `users` (or `roles`) filter, which
is configuration (see [the merge prompt guide](../operations/green-pr-merge.md)).

After the fence, the port reads the PR and refuses `pr_not_open` (closed or
already merged) and `head_moved` (its head is no longer `sha`). Then it calls
`PUT /repos/{repo}/pulls/{number}/merge` with `sha` and `merge_method:
squash`. GitHub answers `409` when the head moved and `405` when it cannot
merge the PR; every 4xx except 408 fails the step without retry. The REST
merge has no idempotency key, so the port claims `repo#number#sha` first: an
outcome it cannot know (a 5xx, a timeout, a lost answer) is never sent blindly.
A later call (the engine's retry, or another click) reads the PR first:

- merged, it completes with the merge commit;
- still open at exactly `sha`, the merge did not happen, so it is sent again
  (GitHub merges a PR once, and `sha` pins the commit), at most 3 times per
  claim; a claim still in flight is left alone until it is 10 minutes old (a
  crash mid-send);
- moved or closed unmerged, it fails `head_moved` or `pr_not_open` and releases
  the claim;
- otherwise (the re-sends spent, a claim in flight) it fails
  `merge_outcome_unknown`. Check the PR on GitHub; the claim is the
  `github_merge_once` document for that repo, PR and sha.

With `delete_branch: true`, after a merge and only when the PR's head is in
`repo` itself (never a fork's), the branch is deleted. A branch already gone is
not a failure; any other delete error is reported in `branch_delete_error`
and the merge still counts.

Outputs: `merged` (boolean), `sha` (the merge commit GitHub made),
`branch_deleted`, `branch_delete_error` (an error code, or `null`) and, on a
repeat, `skipped` (`merged_before` or `merged_unconfirmed`).

The `merge` role ships **with no digest**: the merge workflow is live
configuration, so until a release pins its digest in
`culture_rules/actors/trusted.py`, `github.merge` refuses every run with
`workflow_not_trusted`. The workflow's id must be `merge`. The release step is
in [the guide](../operations/green-pr-merge.md#3-pin-the-merge-workflow).

## Troubleshooting

| Symptom | Cause |
|---|---|
| Every delivery shows `401` | The App has no webhook secret, the secret differs from the grant value, or the API was not restarted with the injection |
| Deliveries show a login page (`200` HTML) or `302` | The Access bypass for `hooks/github` is missing |
| A comment fails `repo_not_allowed` | The repository is not in `connection.repos` |
| A comment fails `actor_misconfigured` | `app_id` or `installation_id` is empty |
| A comment fails `secret_unavailable` | The node was not started with the private-key injection |
| A merge fails `workflow_not_trusted` | The merge workflow's digest is not pinned in the `merge` role, or the workflow was edited after the pin |
| A merge fails `head_moved` or `http_409` | The PR got a new commit after the prompt; nothing was merged |

## On rules.culture.dev

| Item | Value |
|---|---|
| Actor id, machine | `github-app`, `spark` |
| App | `rules-culture-dev`, App ID `5183824`, installation `167755039` (all `agentculture` repositories) |
| Bot identity | `rules-culture-dev[bot]`, user id `337624453`, commit email `337624453+rules-culture-dev[bot]@users.noreply.github.com` |
| Live permissions | Contents RW, Checks R, Actions RW (kept on purpose; the fixer only reads), Issues RW, Pull requests RW, Metadata R; push verified 2026-10-06 |
| Access bypass app | `rules.culture.dev/hooks/github` |
