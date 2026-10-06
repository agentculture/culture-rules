# pr-fixer live evidence log (input for /validate-delivery)

## 2026-10-06 — t16 App permissions (live probe on agentculture/culture-rules, PR #14)

- Installation 167755039 permissions after operator approval: contents write, checks read, actions **write**, issues write, pull_requests write, metadata read; events include check_suite, workflow_run, pull_request_review_comment.
- `GitHubApp.push_token("agentculture/culture-rules")` minted a single-repo token; it pushed `cbb0d1fac49d71d81956fc252c4206f1f8396a38` to PR #14's head branch (non-force).
- GitHub reports author, committer and login `rules-culture-dev[bot]` for that commit.
- The App read 11 check runs on that SHA; the App push triggered CI (lint, test, web, harness-smoke, version-check, GitGuardian).
- The same token was refused (git rc 128) for agentculture/cultureagent.
- PR #14 closed, branch deleted.
