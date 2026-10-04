# Jira app actor

Jira Cloud sends a system webhook when issues and comments change. The
receiver refetches each issue through a service account, and the
`jira.comment` action comments as that account.

This assumes you are signed in to Atlassian as an organisation admin and Jira
administrator, and that nothing else is prepared. Read [README.md](README.md)
first for the prerequisites and how secrets work.

The secrets this guide creates:

| grant name (suggested) | What it is | Used by |
|---|---|---|
| `RULES_JIRA_SERVICE_TOKEN` | the service account's scoped API token | API and engine node |
| `RULES_JIRA_WEBHOOK_TOKEN` | random value that authenticates each delivery | API |

## 1. Create a service account

1. Open **admin.atlassian.com**, choose your organisation, then **Directory**,
   **Service accounts**, **Create a service account**. Name it, for example
   `culture-rules`.
2. Give it the **Jira** app role (User). Then in Jira, add it to each project
   rules should read and comment on: **Project settings**, **People**, **Add
   people**, role **Member**. It can also join a group that already has access
   to all projects.
3. Note the account's **email**. It looks like
   `<name>-<id>@serviceaccount.atlassian.com`.

## 2. Create its API token and seal it

1. On the service account's page, choose **Create credentials**, **API token**.
2. Pick scopes: `read:jira-work` and `write:jira-work`, or the granular read
   issue, read comment and write comment scopes. Set an expiry and create it.
   The token is shown only once.
3. On the serving host:

   ```bash
   grant set RULES_JIRA_SERVICE_TOKEN - --hidden     # paste the token, then Enter and Ctrl-D
   ```

**A scoped token works only through the Atlassian gateway**, not your site URL,
which answers it with 401. Find your site's cloud id:

```bash
curl -s https://<site>.atlassian.net/_edge/tenant_info
# {"cloudId":"<cloud id>"}
```

The API base is `https://api.atlassian.com/ex/jira/<cloud id>`. Check the
token:

```bash
grant run --inject T=RULES_JIRA_SERVICE_TOKEN -- sh -c \
  'curl -s -o /dev/null -w "%{http_code}\n" -u "<service account email>:$T" \
     "https://api.atlassian.com/ex/jira/<cloud id>/rest/api/3/myself"'
```

This should print `200`. While you're there, note the account's `accountId`
from that `/myself` response, without `-o /dev/null`; it is the
`self_identity`.

## 3. Create the webhook token

```bash
grant generate RULES_JIRA_WEBHOOK_TOKEN --hidden --bytes 32 --encoding hex
```

## 4. Open the webhook path in Cloudflare Access

Jira cannot sign in through Access. Give exactly this one path its own bypass,
which the receiver protects with the token:

1. In Cloudflare **Zero Trust**, open **Access**, **Applications**, **Add an
   application**, **Self-hosted**.
2. Fill in:
   - **Application name:** `<rules host> webhook jira`.
   - **Domain:** `<rules host>`.
   - **Path:** `hooks/jira`.
3. Add a policy: **Action: Bypass**, **Include: Everyone**. Save.

Never use `hooks/*` or an empty path. A `POST` to
`https://<rules host>/hooks/jira` should now answer `401`, not `302`.

## 5. Give the services the secrets

The API checks the webhook token and refetches issues with the service token.
The node posts comments with the service token. The README shows how to add an
injection.

```text
# culture-rules-api
--inject CULTURE_RULES_SECRET_RULES_JIRA_WEBHOOK_TOKEN=RULES_JIRA_WEBHOOK_TOKEN
--inject CULTURE_RULES_SECRET_RULES_JIRA_SERVICE_TOKEN=RULES_JIRA_SERVICE_TOKEN
# culture-rules-node
--inject CULTURE_RULES_SECRET_RULES_JIRA_SERVICE_TOKEN=RULES_JIRA_SERVICE_TOKEN
```

```bash
systemctl --user restart culture-rules-api culture-rules-node
```

## 6. Create the actor

In the editor's **Actors** tab, add an **App** actor and set **Machine** to the
serving host, or write the JSON with the CLI:

```json
{
  "id": "jira-app",
  "name": "Jira",
  "kind": "app",
  "machine": "<serving host>",
  "params": {
    "surface": "jira",
    "events": ["jira.issue.created", "jira.issue.updated", "jira.comment.created"],
    "actions": ["jira.comment"],
    "self_identity": "<service account accountId>",
    "connection": {
      "site": "<site>.atlassian.net",
      "api_base": "https://api.atlassian.com/ex/jira/<cloud id>",
      "email": "<service account email>",
      "token": "grant:RULES_JIRA_SERVICE_TOKEN",
      "webhook_token": "grant:RULES_JIRA_WEBHOOK_TOKEN"
    }
  }
}
```

Leave `projects` out to accept every project, including ones created later. Set
`"projects": ["SCRUM"]` to keep only those keys.

## 7. Register the system webhook

Do this after step 5, once the API runs with the token injected. Before that,
every delivery answers `401` and Jira backs off.

1. Print the webhook URL once in your own terminal:

   ```bash
   grant run --inject T=RULES_JIRA_WEBHOOK_TOKEN -- \
     sh -c 'printf "https://<rules host>/hooks/jira?token=%s\n" "$T"'
   ```

2. In Jira, open the **cog**, **System**, **WebHooks** (under **Advanced**). The
   direct address is `https://<site>.atlassian.net/plugins/servlet/webhooks`.
   Click **Create a WebHook**:

   | Field | Value |
   |---|---|
   | Name | `culture-rules` |
   | Status | Enabled |
   | URL | the line printed above |
   | Secret | leave empty (see the note below) |
   | Issue related events: JQL | empty for every project, including future ones; or `project in (SCRUM)` |
   | Issue | tick **created** and **updated** |
   | Comment | tick **created** |
   | Exclude body | unticked |

3. Click **Create**, then clear the URL from your terminal.

**If the form has a Secret field**, you can use it instead of the URL token:

1. `grant generate RULES_JIRA_WEBHOOK_SECRET --hidden --bytes 32 --encoding hex`.
2. Print it once and paste it into **Secret**.
3. Use the URL without `?token=`.
4. Add `"webhook_secret": "grant:RULES_JIRA_WEBHOOK_SECRET"` to the actor's
   `connection`, and inject it into the API unit.

Jira then signs each delivery (`X-Hub-Signature`), and the signature takes
precedence over the token.

The receiver strips the query string from its access log, so the token is not
logged.

## 8. Verify

1. Edit an issue in a covered project, or add a comment.
2. On Jira's **WebHooks** page, the delivery shows its HTTP status:

   | Status | Meaning |
   |---|---|
   | `202` | Accepted and recorded |
   | `200` | A redelivery already recorded, or an event type we don't handle (`{"ignored": true}`) |
   | `401` | Wrong or missing `?token=`, or the API lacks the token injection |
   | `502` | The token passed but the issue refetch failed (service token, gateway, or project access); Jira retries |
   | `302` or a login page | The Access bypass for `hooks/jira` is missing |

3. A rule with an **Event** trigger `jira.issue.updated` fires. Its
   `jira.comment` action `{actor: "jira-app", issue: "SCRUM-12", body}` comments
   as the service account.

## Events and data

| Jira `webhookEvent` | Event type |
|---|---|
| `jira:issue_created` | `jira.issue.created` |
| `jira:issue_updated` | `jira.issue.updated` |
| `comment_created` | `jira.comment.created` |

The payload is only a hint. Each issue key is refetched, and `data` holds
`key`, `project`, `summary`, `status`, `assignee` (accountId), `updated` and
`url`. Pick a project in a rule with a condition on `data.project`. A
redelivery is deduplicated by `X-Atlassian-Webhook-Identifier`.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `401` from Jira on refetch or comment | The scoped token is used against the site URL; set `api_base` to the gateway |
| Every delivery answers `401` | The `?token=` differs from the grant value, or the API was not restarted with the injection |
| Deliveries answer `502` | The service account cannot see the project, or its token expired |
| A project's events never arrive | It is excluded by the webhook's JQL or by `connection.projects` |

## On rules.culture.dev

| Item | Value |
|---|---|
| Actor id, machine | `jira-app`, `spark` |
| Site, cloud id | `agentculture.atlassian.net`, `0610b05c-63f8-4935-bd7f-a30f907bba8c` |
| Service account | `culture-spark-9lgwfn7mz2@serviceaccount.atlassian.com`, accountId `712020:5e0ae915-ba1a-43ef-bce0-c0d5ff9bb615`. Shared with culture-nodes, so its comments are `self_authored` here too |
| Secrets | `JIRA_SERVICE_ACCOUNT_TOKEN` (the existing culture-nodes token, used for `token`), `RULES_JIRA_WEBHOOK_TOKEN` |
| Projects | all (`SCRUM` today) |
| Access bypass app | `rules.culture.dev/hooks/jira` |
