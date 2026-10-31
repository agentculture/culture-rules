# Repo documents: the devague record in the library

The `repodocs` bundle pushes every top-level repo's devague record into
culture-library (library.culture.dev), where it is stored private and
mirrored to S3 (culture-rules#124). The library only receives; it never runs
git. The push contract is culture-library's `docs/api/repodocs.md`.

## What it pushes

Every 15 minutes, one run of the action kind `repodocs.sync`
(`culture_rules/node/actions/repodocs.py`) walks the directories directly
under `~/git` on spark. A directory is a repo when its `.git` is a real
directory. It skips `.worktrees.*` and `*-worktrees` directories, symlinks
and linked worktrees (whose `.git` is a file).

From each repo it sends every file under `docs/specs/`, `docs/plans/`,
`docs/deliveries/`, `docs/design/` and `.devague/`. That includes gitignored
files, and repos that ignore `.devague` entirely.

| What | When it is sent | `commit` |
|------|-----------------|----------|
| the default-branch version | its blob differs from the one last sent | the default branch's head sha (also `default_branch_head`; `committed_at` is the head's committer time) |
| the working draft | the bytes on disk differ from the last draft sent (or, with none sent, from the default-branch version) | `working` |
| a tombstone | a file sent before is neither on the default branch nor on disk, or its committed version left the default branch while the file stays on disk | the head sha, or `working` for a file never committed |

- **The default branch** is `refs/remotes/origin/HEAD` as last fetched; the
  run never fetches. Without it, the first of `origin/main`, `origin/master`,
  the local `main` and the local `master` that exists is used, and only then
  the checked-out branch. So a feature branch checked out in a repo without
  `origin/HEAD` is never pushed as the default branch.
- **A file deleted on the default branch but still on disk** (an untracked
  leftover, or an older branch checked out) is tombstoned at the head sha, so
  its old committed version stops being current. The library drops the draft
  with the tombstone; a draft is sent again only when the bytes on disk differ
  from the last committed version.
- **History accrues from the first run.** The run sends no older commits.
  A version is pinned to the head the run saw, because the library makes a
  version current only when `commit == default_branch_head`.
- **`project`** is `domains:` and `connected:` from the repo's
  `culture.yaml`, sent with every request. A repo without them sends empty
  lists. A copy (another clone of an origin, see below) never sends it. A block list may be indented or at the key's own indentation. A
  `connected` entry written `org/repo` is sent as
  `https://github.com/org/repo`; a bare name such as `culture-rules` is not
  sent, and the run logs a warning naming it.
- **A repo with no remote**, or whose `origin` is a path on this host (`/…`,
  `./…`, `../…`, `~…` or a `file:` URL), is identified as
  `spark:<absolute path>`.
- **Two full clones of one origin** (for example `~/git/lobes-cli` and
  `~/git/model-gear`, both `https://github.com/agentculture/lobes-cli`) both
  push under that origin, because the library is one org-wide corpus. Origins
  are compared with the host lowercased, the scp and https forms equal, and
  `.git`, a trailing `/` and case in the path ignored. One clone, the
  **owner**, syncs as usual. The owner is the clone whose directory name is
  the origin's repo name (case-insensitive, `-` and `_` equal). With several
  such names, or none, it is the first by directory name. Every other clone
  is a **copy**, logged once per run by name. A copy sends only
  default-branch versions, their spec links and their tombstones. It never
  sends a working draft or a `working` tombstone, so the library's one draft
  per path is the owner's and no longer flips between clones every cycle. A
  copy's committed pushes are normally no-ops. A copy with an older head
  never rolls a version back: the library keeps the newest `committed_at`
  among default-branch versions. Repos with no remote, or with a local-path
  origin, are never grouped.
- **Migration from shared drafts.** The drafts a copy pushed before it was a
  copy are handed to its owner. In that run the owner re-sends its own
  draft of each of those paths once, which replaces the copy's bytes. A path
  the owner has neither in its default branch nor on disk gets a `working`
  tombstone, so a copy-only draft leaves the owner's project. The one-time
  cost is one request per path the copy had drafted. Nothing needs cleaning
  up by hand.
- **`spec_links`** go with each `docs/specs/**.md` version:
  - `trailer_commits` are the commits with a `Spec: <slug>` trailer;
  - `merge_commit` and `pr` come from the first first-parent commit that
    added `docs/deliveries/*<slug>.md`;
  - `code_paths` are the files the trailer commits touched or, without
    trailers, the files that merge changed. They are followed through
    renames to the head.

  The module docstring has the exact git commands.
- **Only changes are sent.** A state file (`state_path`) records what was
  sent, so an unchanged cycle sends nothing.
- **The output is counts only:** `repos`, `pushed`, `skipped`,
  `tombstoned`, `errors`, `duration_s` and `stopped`. It never carries file
  bytes or paths.

**Cycle length.** The rule fires every 15 minutes (`*/15 * * * *`). An edit
reaches the library as the working draft within one cycle: at most 15
minutes plus the run (bounded by the actor's `max_seconds`, 300). A run that
stops early, at `max_seconds` or `max_files`, sets `stopped: true`. The next
cycle resumes where it stopped, so the very first sync of the whole corpus
may take a few cycles.

**Failures.** A file over 16 MiB, a git error on one file or a non-2xx answer
is counted in `errors` and logged by repo and path, never by content. Such
failures don't stop the run, and the next cycle retries them, except a file
over 16 MiB on disk: it is one error, then skipped until it changes. A
`404 not_found` for a tombstone counts as done. A network error stops the run
(the library is down). A bad actor, an unresolvable grant or a missing
`repos_root` fails the step.

## The bundle

| File | What it is |
|------|------------|
| `actors/culture-library-repodocs.json` | the `service` actor on spark: `base_url` `http://127.0.0.1:8796`, `api_key` `grant:LIBRARY_REPODOCS_PUSH_API_KEY`, `repos_root` `~/git`, `state_path` `~/.local/state/culture-rules/repodocs-sync.json`, `max_seconds` 300 (optionally `max_files`) |
| `workflows/repodocs-sync.json` | one `repodocs.sync` step through that actor (`timeout_s` 900), outputting the counts |
| `rules/repodocs-sync.json` | the schedule trigger `*/15 * * * *` (UTC), placed on spark, `concurrency_key` and `exclusive_group` `repodocs-sync` so runs never overlap; **disabled** |

## Setup

1. **The library side.** The library must run culture-library's repodocs
   push route (agentculture/culture-library#38). Its S3 mirror bucket must
   exist.
2. **The key.** On the culture-library host, mint a write key for these four
   kinds alone, so it can be revoked on its own:

   ```bash
   culture-library keys mint --name culture-rules-repodocs --scope write \
     --kind spec --kind plan --kind devague-record --kind design --private
   ```

   Store it in grant on spark, read from stdin:

   ```bash
   grant set LIBRARY_REPODOCS_PUSH_API_KEY -
   ```

   If you store it `--hidden`, add
   `--inject CULTURE_RULES_SECRET_LIBRARY_REPODOCS_PUSH_API_KEY=LIBRARY_REPODOCS_PUSH_API_KEY`
   to the node unit's `grant run` options (as for the newspaper keys in
   `newspaper.md`), then restart the node.
3. **Import** in this order: actors, workflows, rules. Each import is a dry
   run without `--apply`:

   ```bash
   culture-rules actors import docs/rules/repodocs --apply
   culture-rules workflows import docs/rules/repodocs --apply
   culture-rules rules import docs/rules/repodocs --apply
   ```

4. **Enable** the rule:

   ```bash
   culture-rules rules enable repodocs-sync --apply
   ```

## Verify

Check the runs and their counts:

```bash
culture-rules runs list --rule-id repodocs-sync --limit 3
culture-rules runs show <run id> --json
```

A good run has `status: succeeded`, `errors: 0` and, once the first sync has
caught up, `stopped: false`.

Then, from a culture-library checkout, compare files on disk with the library
and S3 for a sample of repos. Include at least one repo that gitignores
`.devague`, such as `notion-agent`:

```bash
CULTURE_LIBRARY_API_KEY=<a read key with private access> \
  scripts/verify-mirror.py --repos culture-rules,culture-library,steward,devague,notion-agent
```

It must report 0 missing and 0 mismatched.

## Notes

- **Resetting the state.** Deleting the state file makes the next run resend
  every file. That is harmless: an identical re-push is a 200 no-op. A
  version whose head has moved since is stored under the new head sha.
- **A repo moved or deleted** under `~/git` keeps its library docs; its old
  entries stay in the state file and get no tombstones.
- **The commands.** Every git call is an argv list (no shell), with explicit
  `--git-dir`/`--work-tree` and a 60 s timeout each.
