# Conflict Resolution Guide

When `stackman sync` encounters a rebase conflict, it can either:
1. **Interactively** — wait for you to manually resolve and run `git rebase --continue`
2. **Automatically** — invoke a resolver command (e.g., an AI model) to resolve conflicts unattended

## Using the Built-in Prompt

Stackman provides an **opinionated, battle-tested prompt** for conflict resolution. It inlines the conflict-resolution methodology with safety guardrails and clear exit instructions.

### View the Prompt

To see the prompt template that will be used:

```bash
stackman show-resolver-prompt
```

This displays the prompt **with environment variables filled in** (e.g., `{STACKMAN_BRANCH}` becomes your actual branch name). This is what an AI resolver will see.

To see the **raw template** with placeholders:

```bash
stackman show-resolver-prompt --template
```

### Quick Start: Claude CLI

If you have the `claude` CLI installed, the simplest way is:

```bash
stackman sync feature --resolver "claude -p @prompt"
```

The `@prompt` token is automatically expanded to the default conflict resolution prompt (with all `STACKMAN_*` variables filled in).

Or set it as your default:

```bash
export STACKMAN_RESOLVER="claude -p @prompt"
stackman sync feature
```

**What is `@prompt`?**  
Stackman expands `@prompt` to the full default conflict resolution prompt with your context (branch name, conflicted files, etc.) already substituted. You stay in control—you're just telling stackman where to inject it in your command.

### Extending the Prompt

The built-in prompt is generic and works well for most codebases. You can extend it with project-specific guidance:

```bash
PROMPT="$(stackman show-resolver-prompt)"
PROMPT="${PROMPT}

## Project-Specific Notes
- Always preserve the types in merge conflicts
- If uncertain, choose the version that matches our branch strategy
- Run 'make test' to validate syntax"

stackman sync feature --resolver "claude -p \"$PROMPT\""
```

### Using Different AI Models

#### Claude API

```bash
#!/bin/bash
# File: ~/.local/bin/resolve-conflicts-claude-api

BRANCH="$STACKMAN_BRANCH"
PROMPT="$(stackman show-resolver-prompt)"

curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d "{
    \"model\": \"claude-opus-4-1\",
    \"max_tokens\": 4096,
    \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT\"}]
  }" | jq -r '.content[0].text'

exit $?
```

Then:

```bash
chmod +x ~/.local/bin/resolve-conflicts-claude-api
stackman sync feature --resolver "~/.local/bin/resolve-conflicts-claude-api"
```

#### OpenAI GPT-4

```bash
#!/bin/bash
# File: ~/.local/bin/resolve-conflicts-openai

BRANCH="$STACKMAN_BRANCH"
PROMPT="$(stackman show-resolver-prompt)"

curl -s https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"gpt-4\",
    \"messages\": [
      {\"role\": \"system\", \"content\": \"You are a Git conflict resolver.\"},
      {\"role\": \"user\", \"content\": \"$PROMPT\"}
    ],
    \"temperature\": 0.3
  }" | jq -r '.choices[0].message.content'

exit $?
```

Then:

```bash
chmod +x ~/.local/bin/resolve-conflicts-openai
stackman sync feature --resolver "~/.local/bin/resolve-conflicts-openai"
```

## Environment Variables

When your resolver is invoked, stackman populates these environment variables:

### Required Context
- `STACKMAN_BRANCH` — The branch being rebased
- `STACKMAN_PARENT` — The parent branch
- `STACKMAN_PARENT_TIP` — SHA of the parent branch tip
- `STACKMAN_FORK_POINT` — SHA where the branch forked from parent
- `STACKMAN_CONFLICTED_FILES` — Newline-separated list of conflicted files
- `STACKMAN_OPERATION` — Always `"rebase"` (for future extensibility)

### Optional Context
- `STACKMAN_REPO_URL` — Origin URL (if configured in Git)
- `STACKMAN_PARENT_PR_NUMBER` — GitHub PR number of parent (if available)
- `STACKMAN_PR_NUMBER` — GitHub PR number of this branch (if available)

## Exit Code Semantics

Your resolver **must** exit with:

- **0 (success)** — All conflicts resolved, rebase completed, working tree clean
  - The resolver should have:
    - Read each conflicted file
    - Resolved conflicts (removed markers)
    - Staged files with `git add <file>`
    - Run `git rebase --continue` (and waited for it to complete)
    - Left the working tree clean

- **1 (failure)** — Could not resolve safely, or rebase was aborted
  - Use this when:
    - A conflict is ambiguous or unclear
    - Merging would break code logic
    - You're unsure about the safety of the resolution
    - The resolver ran `git rebase --abort` due to safety concerns

## Exit Criteria Checklist for Resolvers

When exiting with 0 (success), the resolver **must verify**:

1. ✓ No rebase is in progress (`git rebase --continue` completed)
2. ✓ HEAD is at the target commit (all commits replayed)
3. ✓ Working tree is clean (no uncommitted changes)
4. ✓ All files have been staged (git status shows nothing)

If any of these fail, exit 1 and let the user manually resolve.

## Safety Philosophy

The built-in prompt emphasizes safety over aggressive automation. Key principles:

1. **Default to abort** — When in doubt, fail safely rather than guess
2. **Understand intent** — Merge based on what each side is trying to do, not just text
3. **Verify syntax** — Resolved files should parse and validate
4. **Staged explicitly** — Don't assume; each file must be explicitly staged

This is intentional: it's better to fail and ask for manual intervention than to silently break code.

## Tips & Best Practices

### 1. Test with a Simple Conflict First

Before relying on your resolver for complex stacks, test it on a single conflicted branch:

```bash
stackman sync feature --resolver "your-resolver-command"
```

This lets you see the resolver's output and verify it works as expected.

### 2. Log Resolver Output

Your resolver's stdout and stderr are captured and printed by stackman. Use this for debugging:

```bash
stackman sync feature --resolver "your-resolver-command 2>&1"
```

### 3. Extend the Prompt for Your Codebase

If your codebase has specific conventions or gotchas, extend the prompt:

```bash
PROMPT="$(stackman show-resolver-prompt)"
PROMPT="${PROMPT}

## Our Codebase Standards
- TypeScript: strict mode, types always first
- React: hooks first, no class components
- Imports: side effects last
- Functions: pure when possible"

export STACKMAN_RESOLVER="claude -p \"\$PROMPT\""
```

### 4. Use with CI/CD

Set `STACKMAN_RESOLVER` in your CI environment to resolve conflicts automatically:

```yaml
# GitHub Actions
- name: Sync stack
  env:
    STACKMAN_RESOLVER: "claude -p \"$(stackman show-resolver-prompt)\""
  run: stackman sync feature
```

### 5. Manual Override

Even with a resolver configured, you can always force interactive mode with `--no-wait=false` (TTY detection) or resolve manually:

```bash
# Force interactive (no resolver)
stackman sync feature --no-wait

# Manual resolution
git rebase --continue  # or git rebase --abort
stackman sync feature  # retry
```

## Troubleshooting

### Resolver Never Completes

Stackman does not impose a timeout on the resolver — it waits until the
resolver exits, so a hung resolver will block the sync indefinitely (interrupt
it with Ctrl-C). Check that your resolver:

1. Actually completes the rebase (`git rebase --continue`)
2. Doesn't wait for interactive input after completing
3. Exits cleanly with status 0 or 1

### Resolver Exits 0 but Rebase Didn't Complete

Stackman validates that the rebase actually succeeded by checking:
1. No rebase is in progress
2. HEAD is at the target commit
3. Working tree is clean

If any check fails, stackman returns an error even if the resolver exited 0. This is a safety feature.

### Files Still Have Conflict Markers

If your resolver exits 0 but conflict markers remain, stackman will fail validation. Ensure your resolver:

1. Reads each conflicted file
2. Removes **all** markers: `<<<<<<<`, `=======`, `>>>>>>>`
3. Runs `git add <file>` for each resolved file

### Resolver Can't Handle Large Prompts

If you're extending the prompt heavily, be aware that:

1. Long prompts may hit token limits in your AI model
2. Some CLI invocations have shell argument limits
3. Consider using a wrapper script instead of inline shell expansion

Example wrapper script approach:

```bash
#!/bin/bash
# File: ~/.local/bin/my-resolver

# Read STACKMAN_* vars; build a detailed prompt locally
PROMPT=$(cat << 'EOF'
$(stackman show-resolver-prompt)

[Additional context and project-specific guidance]
EOF
)

claude -p "$PROMPT"
```

## See Also

- `stackman show-resolver-prompt` — Display the template
- `stackman sync --help` — CLI options for conflict resolution
- `src/stackman/resolver_prompt.py` — Prompt source in the repo
