# GitHub app actor

A dedicated GitHub App, `rules-culture-dev`, receives repository events and
comments as itself.

| Item | Value on rules.culture.dev |
|---|---|
| Actor id | `github-app` (machine `spark`) |
| App slug / bot login | `rules-culture-dev` / `rules-culture-dev[bot]` |
| App ID | `5183824` |
| Installation ID | `167755039` (the `agentculture` org, all repositories) |
| Webhook URL | `https://rules.culture.dev/hooks/github` |
| Secrets (grant, hidden) | `RULES_GITHUB_APP_PRIVATE_KEY`, `RULES_GITHUB_WEBHOOK_SECRET` |

## 1. Create the App

1. Go to GitHub, then **Settings**, **Developer settings**, **GitHub Apps**,
   **New GitHub App**. Use the organisation's settings for an org-owned App.
2. Set **Webhook URL** to `https://rules.culture.dev/hooks/github`. Generate a
   random **webhook secret** and enter it in the **Webhook secret** field.
   **The App must have a secret.** Without one, GitHub sends unsigned
   deliveries and the receiver answers every one with 401.
3. Set **Permissions**: Issues read and write, Pull requests read and write,
   Metadata read.
4. Under **Subscribe to events**, select `Pull request`, `Issue comment`,
   `Issues` and `Pull request review`.
5. Create the App, then **Generate a private key**, which downloads a `.pem`.
   Note the **App ID**.

## 2. Install it

On the App's page, open **Install App** and choose the organisation. Pick all
repositories or a selection. The installation ID is the number at the end of
the installation's settings URL (`.../installations/<id>`).

## 3. Seal the secrets

```bash
grant set RULES_GITHUB_APP_PRIVATE_KEY - --hidden < rules-culture-dev.private-key.pem
grant set RULES_GITHUB_WEBHOOK_SECRET - --hidden     # paste the webhook secret
```

Then delete the downloaded `.pem`.

## 4. Let the services use the secrets

The API verifies deliveries with the webhook secret. The engine node signs the
App's JWT with the private key. Add both injections to the API unit and to the
node unit on the actor's machine:

```text
--inject CULTURE_RULES_SECRET_RULES_GITHUB_APP_PRIVATE_KEY=RULES_GITHUB_APP_PRIVATE_KEY
--inject CULTURE_RULES_SECRET_RULES_GITHUB_WEBHOOK_SECRET=RULES_GITHUB_WEBHOOK_SECRET
```

Restart both units afterwards. On a node installed with `install.sh`, pass
`--secret RULES_GITHUB_APP_PRIVATE_KEY --secret RULES_GITHUB_WEBHOOK_SECRET`.

## 5. Define the actor

```json
{
  "id": "github-app",
  "name": "GitHub (rules-culture-dev App)",
  "kind": "app",
  "machine": "spark",
  "params": {
    "surface": "github",
    "events": ["github.pr.opened", "github.pr.closed", "github.pr.reopened",
               "github.comment.created", "github.issue.opened", "github.review.submitted"],
    "actions": ["github.comment"],
    "self_identity": "rules-culture-dev[bot]",
    "connection": {
      "app_id": "5183824",
      "installation_id": "167755039",
      "private_key": "grant:RULES_GITHUB_APP_PRIVATE_KEY",
      "webhook_secret": "grant:RULES_GITHUB_WEBHOOK_SECRET",
      "repos": ["agentculture/culture-rules"]
    }
  }
}
```

`repos` is the allow-list for **comments**: the App may only comment on these
repositories. Events are recorded from every repository the App is installed
on. On rules.culture.dev, `repos` lists all repositories in the installation.
When the App gains a repository, add it to the list.

## Events and data

| GitHub event / action | Event type |
|---|---|
| `pull_request` opened / closed / reopened | `github.pr.opened` / `.closed` / `.reopened` |
| `issue_comment` created | `github.comment.created` |
| `issues` opened | `github.issue.opened` |
| `pull_request_review` submitted | `github.review.submitted` |

The event `data` holds `repository`, `number`, `title`, `url`, `author` and
`action`, plus `merged` for PRs, `comment` (truncated) for comments, and
`review_state` for reviews. A redelivery is deduplicated by
`X-GitHub-Delivery`.

The action is `github.comment` with `{actor: "github-app", repo, number, body}`.

## Verify

- **The App reaches us.** Under the App's **Advanced** settings, **Recent
  Deliveries** should show `202` for new events. A `401` means the App has no
  webhook secret, the secret differs from the grant value, or the API was not
  started with the injection.
- **A local signed probe.** A signed `ping` answers `200 {"pong": true}`. An
  unsigned request answers `401`.
- **The token exchange works.** The first `github.comment` run succeeds and
  returns `comment_id` and `url`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Every delivery answers `401` | No webhook secret on the App, or the value does not match the grant secret |
| `401` only after the hidden secrets were sealed | The service was not restarted with `--inject CULTURE_RULES_SECRET_...` |
| A comment fails `repo_not_allowed` | The repository is not in `connection.repos` |
| A comment fails `actor_misconfigured` | `app_id` or `installation_id` is empty |
| Deliveries return a login page (200, HTML) | The Access bypass for `/hooks/github` is missing |
