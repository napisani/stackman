# stackman — design

**Status:** implemented / evolving  
**Location:** `pub/stackman/`

## Purpose

`stackman` is a small CLI for local stacked-branch workflows. It tracks branch lineage in a SQLite database so a full stack can be rebased in the right order after an upstream branch moves.

It complements normal Git usage: you still create branches with Git, resolve conflicts with Git, and push with Git. Stackman records enough metadata to make repeated stack syncs predictable.

## User-facing CLI

The CLI is branch-first. Commands can be run from any worktree in the repository; the current branch is only a default selector.

```bash
stackman                         # show current branch tracking status
stackman --version               # print version and exit
stackman status [BRANCH] [--json]
stackman track [BRANCH] --parent PARENT
stackman chain ANCHOR BRANCH...
stackman sync [BRANCH] [--allow-dirty] [--resolver CMD] [--no-wait]
stackman done [BRANCH]
stackman list [--json]
stackman forget [BRANCH]
stackman forget --all [--global] [--dry-run] [-y]
stackman gh discover PR_NUMBER [--apply]
stackman gh discover-mine [--apply]
```

The global `--db-path` and `--repo` options may be given either before the
subcommand (`stackman --repo X list`) or after it (`stackman list --repo X`);
a subcommand-level value overrides the top-level one.

### Non-interactive use (scripts / agents)

A TTY is never required. `status` and `list` accept `--json`, which emits a
single JSON object to stdout and nothing else; `status --json` exits `0` whether
or not the branch is tracked (read the `tracked` field). Destructive bulk
operations (`forget --all`) require `-y`/`--yes` when stdin is not a TTY rather
than blocking on a prompt.

### Shell completion

`BRANCH` arguments complete from tracked branch names (`sync`/`done`/`forget`/
`status`) or all local branches (`track`/`chain`). Completion is Click-native;
enable it per shell, e.g. for Zsh:

```bash
eval "$(_STACKMAN_COMPLETE=zsh_source stackman)"   # bash_source / fish_source also supported
```

### Output conventions

Streams and voice are deliberate so output is predictable for humans and scripts:

- **stdout** carries the command's result: tracking confirmations, the `list`
  tree, `status` fields, the `gh discover` plan, and (for `--json`) exactly one JSON
  object and nothing else.
- **stderr** carries diagnostics, prompts, and errors — including the
  `forget --all` confirmation prompt and every failure message.
- Progress narration during a multi-step operation (notably `sync`) is prefixed
  with `[stackman]` and written to stdout; final result lines are unprefixed.
  `sync` progress is intentionally on stdout (not stderr) because it is the
  command's primary human-facing report and downstream tooling parses it.
- Query commands never require a TTY. Confirmations for destructive bulk
  operations are the only interactive prompts, and they are always bypassable
  with `--yes`.

### Command semantics

| Command | Role |
|---------|------|
| `stackman` | Show tracking state for the current branch. |
| `stackman status [BRANCH] [--json]` | Show tracking state for `BRANCH` (defaults to current). `--json` emits a machine-readable object and exits 0 even when untracked. |
| `stackman track [BRANCH] --parent PARENT` | Register or update one branch with its parent and fork point. `BRANCH` defaults to the current branch. |
| `stackman chain ANCHOR BRANCH...` | Register an existing linear stack. `ANCHOR` is not tracked; every later branch points at the previous item. |
| `stackman sync [BRANCH]` | Sync the full stack containing `BRANCH`; `BRANCH` defaults to the current branch. |
| `stackman done [BRANCH]` | Mark a branch as done: remove it from Stackman tracking and reparent its children onto its recorded parent. Does not delete Git branches. |
| `stackman list [--json]` | Show tracked branches in the current repository as a stack tree rooted at each anchor; the current branch is marked `(current)`. Colorized only on a TTY (honors `NO_COLOR`). `--json` emits a machine-readable object. |
| `stackman forget [BRANCH]` | Stop tracking a branch without reparenting children. Does not delete Git branches. |
| `stackman forget --all [--global] [--dry-run] [-y]` | Forget all tracked branches in the current repository (or every repository with `--global`). `--dry-run` lists what would be removed and changes nothing. Prompts for confirmation unless `-y`/`--yes` (required when stdin is not a TTY). Does not delete Git branches. |
| `stackman gh discover PR_NUMBER [--apply]` | Use `gh` to discover an open PR stack from a PR number. Read-only by default; `--apply` imports local branches into Stackman. |
| `stackman gh discover-mine [--apply]` | Use `gh pr list --author @me` to plan every open PR authored by you: tracks each PR's head branch onto its base branch. Read-only by default; `--apply` writes the metadata. |

## Core model

Stackman tracks a branch dependency tree per Git repository.

The canonical tracked-branch fields are:

- repository key
- branch name
- parent branch name
- fork-point SHA

A stack label is internal metadata used to find the full stack containing a selected branch. Users do not manage stack labels directly. `track` creates a new opaque stack label for a root branch; child branches inherit their tracked parent's label. `chain` creates one opaque label for the whole chain.

Repository identity uses `git rev-parse --git-common-dir`, so linked worktrees for one clone share the same Stackman metadata.

## Tracking

### `track`

`stackman track [BRANCH] --parent PARENT`:

1. Resolve the repository from `--repo` or the current directory.
2. Use `BRANCH` or the current branch.
3. Validate both `BRANCH` and `PARENT` exist locally.
4. Store `parent_branch_name = PARENT`.
5. Store `fork_point_sha = git merge-base BRANCH PARENT`.
6. Replace any previous internal stack metadata for `BRANCH`.
7. If `PARENT` is tracked and has a stack label, inherit that label.
8. Otherwise create a new opaque stack label anchored at `PARENT`.

### `chain`

`stackman chain ANCHOR BRANCH...` is the batch form for an existing linear stack.

For example:

```bash
stackman chain main feature-a feature-b feature-c
```

records:

```text
feature-a -> main
feature-b -> feature-a
feature-c -> feature-b
```

and applies one new opaque stack label to all tracked branches in the chain.

## Sync

`stackman sync [BRANCH]` uses `BRANCH` only as the selector. It syncs the full stack containing that branch.

Resolution:

1. Find the selected branch's single stack label.
2. Find all branches in the current repo carrying that label.
3. Walk upward through stored parents to find the stack root(s), stopping at the stack anchor, an untracked parent, or a trunk branch.
4. Include all tracked descendants below those roots, even descendants without the selected label.
5. Rebase in topological order: parents before children.

Per branch:

1. Determine the current tip of the branch's sync parent.
2. Optionally squash post-fork commits when `--squash` is passed.
3. Run `git rebase --onto <parent-tip> <stored-fork-point>`.
4. If a conflict occurs:
   - **Interactive mode** (when stdin is available and `--no-wait` is not set): keep Stackman paused while the user resolves with `git rebase --continue` or aborts with `git rebase --abort`.
   - **Non-interactive mode with resolver** (when `--resolver <cmd>` is provided or `STACKMAN_RESOLVER` is set): invoke the resolver command to automatically resolve the conflict.
   - **Non-interactive mode without resolver**: exit with an error directing the user to provide `--resolver <cmd>` or resolve manually.
5. After a successful rebase, update `fork_point_sha` to the parent tip used for that rebase.
6. Push with `--force-with-lease` when the branch has an upstream.

### Hands-off sync with `--resolver`

When `--resolver <cmd>` is provided (or `STACKMAN_RESOLVER` is set), Stackman can resolve merge conflicts automatically:

```bash
stackman sync --resolver "path/to/stackman-resolve-conflicts"
STACKMAN_RESOLVER="path/to/stackman-resolve-conflicts" stackman sync
```

The resolver command receives conflict context via environment variables:

- `STACKMAN_BRANCH` — branch being rebased
- `STACKMAN_PARENT` — parent branch name
- `STACKMAN_PARENT_TIP` — commit SHA of parent tip (rebase `--onto` target)
- `STACKMAN_FORK_POINT` — commit SHA of fork-point (rebase upstream)
- `STACKMAN_CONFLICTED_FILES` — newline-separated list of files with conflicts
- `STACKMAN_OPERATION` — operation type (currently "rebase")
- `STACKMAN_REPO_URL` — remote origin URL (auto-discovered, optional)
- `STACKMAN_PARENT_PR_NUMBER` — GitHub PR number for parent (auto-discovered via `gh`, optional)
- `STACKMAN_PR_NUMBER` — GitHub PR number for branch (auto-discovered via `gh`, optional)

The resolver must:
1. Resolve conflicts (e.g., via an agent that runs `git add` and `git rebase --continue`)
2. Exit 0 on success, nonzero on failure

A reference resolver script is provided at `priv/skills/stackman-resolve-conflicts/stackman-resolve-conflicts`, which launches a headless agent with conflict-resolution instructions inlined.

### `--no-wait`

Force non-interactive mode, bypassing TTY detection. Use this when stdin is available but you want to use a resolver instead of the interactive prompt:

```bash
stackman sync --resolver <cmd> --no-wait
```

Before a non-dry-run sync, Stackman checks only worktrees involved in the sync set. Unrelated linked worktrees may be dirty. `--allow-dirty` skips this preflight and lets Git decide whether checkout/rebase can proceed; it is intentionally incompatible with `--squash`.

## Done vs forget

`done` and `forget` both remove Stackman metadata for a branch, but they mean different things:

- `done BRANCH`: branch was merged or is no longer part of the stack. Children are lifted onto `BRANCH`'s recorded parent.
- `forget BRANCH`: stop tracking this branch only. Children are not reparented and may still record the forgotten branch as parent.

Neither command deletes Git branches.

## Discovering existing PR stacks

The `gh` subcommand group is the only GitHub-dependent surface of Stackman. It
shells out to `gh` to read open PR `headRefName` / `baseRefName` metadata; the
rest of Stackman remains Git-provider agnostic and works without `gh` installed.

`stackman gh discover PR_NUMBER` starts from the required PR number, walks
upward through PR base branches until it reaches a non-PR anchor branch, then
traverses the selected stack subtree downward. By default it only prints the
tree and equivalent `stackman track` plan. `--apply` writes tracking metadata
for branches that already exist locally and skips missing local branches
without fetching or deleting anything.

`stackman gh discover-mine` runs `gh pr list --state open --author @me` and
plans every open PR authored by the current user: each PR's head branch is
tracked onto its base branch, forming one or more stacks rooted at non-PR
anchor branches. Like `discover`, it is read-only by default and `--apply`
writes the metadata; branches that do not exist locally are skipped.

## Storage

| Item | Path / mechanism |
|------|------------------|
| Database directory | `$XDG_DATA_HOME/stackman/`, or `~/.local/share/stackman/` when unset |
| Database file | `stackman.db` |

SQLite schema:

- `repos` — canonical repository key
- `branches` — tracked branch lineage and fork point
- `stacks` — internal opaque stack labels and their anchor branch
- `branch_stack_labels` — branch-to-stack-label join table

## Testing strategy

Tests should stay close to real usage:

- real Git repositories on disk
- real linked worktrees where relevant
- real SQLite database files
- subprocess Git commands rather than mocks
- injectable app boundary for `db_path`, `cwd`, `stdin`, `stdout`, and `stderr`

The test suite should exercise the public branch-first interface (`track`, `chain`, `sync`, `done`, `list`, `forget`) while still validating persistence and graph behavior through the store where useful.
