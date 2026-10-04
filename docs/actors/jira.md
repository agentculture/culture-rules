# Jira app actor

Jira Cloud `agentculture.atlassian.net`. A Jira system webhook wakes the
receiver, which refetches each issue through a service account, and the
`jira.comment` action comments as that account.

| Item | Value on rules.culture.dev |
|---|---|
| Actor id | `jira-app` (machine `spark`) |
| Site | `agentculture.atlassian.net` |
| Cloud id | `0610b05c-63f8-4935-bd7f-a30f907bba8c` |
| API base (gateway) | `https://api.atlassian.com/ex/jira/0610b05c-63f8-4935-bd7f-a30f907bba8c` |
| Service account | `culture-spark-9lgwfn7mz2@serviceaccount.atlassian.com` (shared with culture-nodes) |
| Service account accountId | `712020:5e0ae915-ba1a-43ef-bce0-c0d5ff9bb615` |
| Projects | all (`SCRUM` today) |
| Secrets (grant, hidden) | `JIRA_SERVICE_ACCOUNT_TOKEN`, `RULES_JIRA_WEBHOOK_TOKEN` |

## 1. The service account and its token

Jira has no "app install" step here. The integration is an Atlassian **service
account** with a **scoped API token**, the same one culture-nodes uses:

1. In **admin.atlassian.com**, open **Directory**, then **Service accounts**.
   Create one or reuse one, give it access to Jira, and add it to the projects
   it should read and comment on.
2. Create a **scoped API token** for it (scopes: read issues and comments,
   write comments) and seal it:

   ```bash
   grant set JIRA_SERVICE_ACCOUNT_TOKEN - --hidden
   ```

**A scoped token works only through the Atlassian gateway.** The site URL
answers it with 401. Set `api_base` to
`https://api.atlassian.com/ex/jira/<cloudId>`, and read the cloud id from
`https://<site>/_edge/tenant_info`.

## 2. The webhook token

The webhook authenticates with a random value in the URL (`?token=`), or, when
the Jira form offers a **Secret** field, with an HMAC signature
(`X-Hub-Signature`). Generate and seal the token without printing it:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32), end='')" \
  | grant set RULES_JIRA_WEBHOOK_TOKEN - --hidden
```

To build the webhook URL you need the value once. Inject it into a command
rather than printing it to a shared terminal.

## 3. Register the system webhook (Jira admin)

In Jira, open **Settings** (cog), **System**, **WebHooks**, **Create a
WebHook**:

- **URL:** `https://rules.culture.dev/hooks/jira?token=<RULES_JIRA_WEBHOOK_TOKEN value>`
- **Events:** Issue created, Issue updated, Comment created.
- **JQL:** leave it empty for every project, or use `project in (SCRUM, ...)`.
  The actor's `connection.projects` can also narrow it.
- **Secret:** if the form offers one, prefer it. Seal a separate value, set the
  actor's `connection.webhook_secret`, and drop the `?token=`.

The receiver strips the query string from its access log, so the token is not
logged.

## 4. Let the services use the secrets

The API verifies deliveries with the webhook token and refetches issues with
the service-account token. The node uses the service-account token for
`jira.comment`. Inject both into the API unit and the node unit on the actor's
machine:

```text
--inject CULTURE_RULES_SECRET_JIRA_SERVICE_ACCOUNT_TOKEN=JIRA_SERVICE_ACCOUNT_TOKEN
--inject CULTURE_RULES_SECRET_RULES_JIRA_WEBHOOK_TOKEN=RULES_JIRA_WEBHOOK_TOKEN
```

## 5. Define the actor

```json
{
  "id": "jira-app",
  "name": "Jira (agentculture)",
  "kind": "app",
  "machine": "spark",
  "params": {
    "surface": "jira",
    "events": ["jira.issue.created", "jira.issue.updated", "jira.comment.created"],
    "actions": ["jira.comment"],
    "self_identity": "712020:5e0ae915-ba1a-43ef-bce0-c0d5ff9bb615",
    "connection": {
      "site": "agentculture.atlassian.net",
      "api_base": "https://api.atlassian.com/ex/jira/0610b05c-63f8-4935-bd7f-a30f907bba8c",
      "email": "culture-spark-9lgwfn7mz2@serviceaccount.atlassian.com",
      "token": "grant:JIRA_SERVICE_ACCOUNT_TOKEN",
      "webhook_token": "grant:RULES_JIRA_WEBHOOK_TOKEN"
    }
  }
}
```

Leave `projects` out to accept every project, including ones created later. Set
`projects: ["SCRUM"]` to keep only those keys.

**`self_identity` is shared with culture-nodes.** Because the service account
is shared, comments culture-nodes posts are tagged `self_authored` here too. A
rule that should react to them sets `include_self` on its trigger.

## Events and data

| Jira `webhookEvent` | Event type |
|---|---|
| `jira:issue_created` | `jira.issue.created` |
| `jira:issue_updated` | `jira.issue.updated` |
| `comment_created` | `jira.comment.created` |

The payload is only a hint: each issue key is refetched, and `data` holds
`key`, `project`, `summary`, `status`, `assignee` (accountId), `updated` and
`url`. Filter by project in a rule with a condition on `data.project`. Any
refetch failure answers `502`, so Jira retries. A redelivery is deduplicated by
`X-Atlassian-Webhook-Identifier`.

The action is `jira.comment` with `{actor: "jira-app", issue: "SCRUM-12", body}`.

## Verify

```bash
# The service account and gateway work (token injected, never printed)
grant run --inject T=JIRA_SERVICE_ACCOUNT_TOKEN -- sh -c \
  'curl -s -o /dev/null -w "%{http_code}\n" -u "<email>:$T" "<api_base>/rest/api/3/myself"'
```

This should print `200`. Then edit an issue; Jira's webhook page shows the
delivery, and a `jira.issue.updated` event lands in the store.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `401` from Jira on refetch or comment | The scoped token is used against the site URL; set `api_base` to the gateway |
| Every delivery answers `401` | The `?token=` differs from the grant value, or the API was not started with the injection |
| Deliveries answer `502` | The refetch failed (token, gateway, or the account cannot see the project) |
| A project's events never arrive | Its key is missing from `connection.projects`, or the webhook's JQL excludes it |
