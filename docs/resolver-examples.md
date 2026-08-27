# Conflict Resolver Examples

This document provides ready-to-use examples for different AI models and setups.

## The `@prompt` Token

Stackman provides a special `@prompt` token that expands to the default conflict resolution prompt. Use it in your resolver command:

```bash
--resolver "claude -p @prompt"
```

Stackman will automatically expand `@prompt` to the full prompt with all `STACKMAN_*` variables (branch name, conflicted files, etc.) already substituted. This keeps your command clean while staying explicit about what's being passed to the resolver.

## Quick Start Examples

### Claude CLI (Simplest)

Use the built-in prompt via the `@prompt` token:

```bash
# One-liner
stackman sync feature --resolver "claude -p @prompt"

# Set as default
export STACKMAN_RESOLVER="claude -p @prompt"
stackman sync feature
```

The `@prompt` token is automatically expanded by stackman to include the default conflict resolution prompt with all your context (branch name, conflicted files, etc.) already filled in.

### With Custom Context

If you want to extend the prompt with project-specific guidance, use `show-resolver-prompt` to see what it includes, then wrap it:

```bash
PROMPT="$(stackman show-resolver-prompt)"
PROMPT="${PROMPT}

## Important for this codebase:
- Always preserve TypeScript strict mode
- Keep imports grouped: React, third-party, local
- If unsure, choose the version that's more recent"

stackman sync feature --resolver "claude -p \"$PROMPT\""
```

Or use a wrapper script (see below) to keep your command clean.

## Resolver Scripts

### Claude (Basic)

The simplest approach—just use the `@prompt` token:

```bash
stackman sync feature --resolver "claude -p @prompt"
```

Or if you prefer a wrapper script:

**File: `~/.local/bin/resolve-with-claude`**

```bash
#!/bin/bash
set -e

# Show progress
echo "[resolver] Resolving conflicts on $STACKMAN_BRANCH"
echo "[resolver] Conflicted files:"
echo "$STACKMAN_CONFLICTED_FILES" | sed 's/^/  /'

# Get the prompt (stackman will expand @prompt, but we can also get it explicitly)
PROMPT="$(stackman show-resolver-prompt)"

# Invoke Claude
claude -p "$PROMPT"

# Exit code from Claude indicates success/failure
exit $?
```

```bash
chmod +x ~/.local/bin/resolve-with-claude
stackman sync feature --resolver "~/.local/bin/resolve-with-claude"
```

### Claude with Preamble

**File: `~/.local/bin/resolve-with-claude-extended`**

```bash
#!/bin/bash
set -e

# Build the full prompt with project context
read -r -d '' FULL_PROMPT << 'PROMPT_END' || true
$(stackman show-resolver-prompt)

## Project-Specific Context

### Code Style
- TypeScript strict mode
- React 18+, hooks first
- Imports: React, third-party, local (grouped)
- Exports: named first, default last

### Conflict Resolution Rules
1. Understand both sides—read commit messages if needed
2. Preserve both intents when safe
3. Choose the "newer" version for refactors
4. Run 'npm run build' to validate TypeScript

### If Unsure
- Check recent git history
- Read PR context if available
- When in doubt, abort safely (exit 1)

PROMPT_END

# Show what we're resolving
echo "[resolver] Branch: $STACKMAN_BRANCH"
echo "[resolver] Parent: $STACKMAN_PARENT"
echo "[resolver] Files: $(echo "$STACKMAN_CONFLICTED_FILES" | wc -l)"

# Invoke Claude with extended prompt
claude -p "$FULL_PROMPT"

exit $?
```

```bash
chmod +x ~/.local/bin/resolve-with-claude-extended
stackman sync feature --resolver "~/.local/bin/resolve-with-claude-extended"
```

### Claude API (Direct)

**File: `~/.local/bin/resolve-with-claude-api`**

```bash
#!/bin/bash
set -e

PROMPT="$(stackman show-resolver-prompt)"

RESPONSE=$(curl -s https://api.anthropic.com/v1/messages \
  -H "x-api-key: ${ANTHROPIC_API_KEY}" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d "{
    \"model\": \"claude-opus-4-1\",
    \"max_tokens\": 4096,
    \"messages\": [
      {
        \"role\": \"user\",
        \"content\": $(jq -Rs . <<< "$PROMPT")
      }
    ]
  }")

# Check for API errors
if echo "$RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
  echo "API Error:" >&2
  echo "$RESPONSE" | jq '.error' >&2
  exit 1
fi

# Extract and output the response
echo "$RESPONSE" | jq -r '.content[0].text'

# Exit 0 if successful, 1 otherwise (Claude will indicate via the response)
exit 0
```

```bash
chmod +x ~/.local/bin/resolve-with-claude-api
export ANTHROPIC_API_KEY="your-key-here"
stackman sync feature --resolver "~/.local/bin/resolve-with-claude-api"
```

### OpenAI GPT-4

**File: `~/.local/bin/resolve-with-openai`**

```bash
#!/bin/bash
set -e

PROMPT="$(stackman show-resolver-prompt)"

RESPONSE=$(curl -s https://api.openai.com/v1/chat/completions \
  -H "Authorization: Bearer ${OPENAI_API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{
    \"model\": \"gpt-4\",
    \"temperature\": 0.3,
    \"messages\": [
      {
        \"role\": \"system\",
        \"content\": \"You are a Git conflict resolver. Resolve conflicts carefully and safely.\"
      },
      {
        \"role\": \"user\",
        \"content\": $(jq -Rs . <<< "$PROMPT")
      }
    ]
  }")

# Check for API errors
if echo "$RESPONSE" | jq -e '.error' > /dev/null 2>&1; then
  echo "API Error:" >&2
  echo "$RESPONSE" | jq '.error' >&2
  exit 1
fi

# Extract and output the response
echo "$RESPONSE" | jq -r '.choices[0].message.content'

exit 0
```

```bash
chmod +x ~/.local/bin/resolve-with-openai
export OPENAI_API_KEY="sk-..."
stackman sync feature --resolver "~/.local/bin/resolve-with-openai"
```

## Setup & Configuration

### Install Resolver Script

```bash
# Create bin directory if it doesn't exist
mkdir -p ~/.local/bin

# Copy one of the scripts above
cp my-resolver.sh ~/.local/bin/resolve-conflicts
chmod +x ~/.local/bin/resolve-conflicts

# Ensure ~/.local/bin is in PATH
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### Set as Default

To always use your resolver without passing `--resolver`:

```bash
# Add to ~/.bashrc or ~/.zshrc
export STACKMAN_RESOLVER="resolve-conflicts"

# Or explicitly:
export STACKMAN_RESOLVER="~/.local/bin/resolve-conflicts"
```

Then just:

```bash
stackman sync feature  # uses resolver automatically
```

### Per-Project Override

Create a `.env` file in your repo root:

```bash
# .env
export STACKMAN_RESOLVER="~/.local/bin/resolve-conflicts"
```

Then source it before running stackman:

```bash
source .env
stackman sync feature
```

## Debugging Resolvers

### View the Prompt

See what the resolver will actually receive:

```bash
stackman show-resolver-prompt
```

### See Resolver Output

Run stackman with your resolver and watch the output:

```bash
stackman sync feature --resolver "~/.local/bin/resolve-conflicts"
```

Stdout and stderr from your resolver are printed so you can see what happened.

### Test Resolver in Isolation

Create a test conflict and run just your resolver:

```bash
# Set up a test branch with conflicts
git checkout -b test-conflicts
# ... make some conflicting changes ...
git rebase main  # create conflicts

# Run just the resolver
~/.local/bin/resolve-conflicts

# Check if it worked
git status
```

### Add Logging

Modify your resolver script to log output:

```bash
#!/bin/bash
set -e

# Log to a file
exec 2>&1 | tee ~/.stackman-resolver.log

echo "[$(date)] Resolver started"
echo "[$(date)] Branch: $STACKMAN_BRANCH"
echo "[$(date)] Files: $STACKMAN_CONFLICTED_FILES"

# ... rest of script ...
```

Then check the log:

```bash
tail -f ~/.stackman-resolver.log
```

## CI/CD Integration

### GitHub Actions

```yaml
name: Sync Stacks
on: [pull_request]

jobs:
  sync:
    runs-on: ubuntu-latest
    env:
      STACKMAN_RESOLVER: "claude -p \"$(stackman show-resolver-prompt)\""
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      - uses: actions/checkout@v3
      - name: Install stackman
        run: |
          # Your installation steps
      - name: Sync stacks
        run: stackman sync
```

### GitLab CI

```yaml
sync-stacks:
  script:
    - export STACKMAN_RESOLVER="claude -p \"$(stackman show-resolver-prompt)\""
    - export ANTHROPIC_API_KEY=$CI_ANTHROPIC_API_KEY
    - stackman sync
```

## Troubleshooting

### Resolver Can't Execute

```bash
# Check it's executable
ls -l ~/.local/bin/resolve-conflicts
# Should show: -rwxr-xr-x

# Make it executable if needed
chmod +x ~/.local/bin/resolve-conflicts

# Test directly
STACKMAN_BRANCH="test" \
STACKMAN_PARENT="main" \
STACKMAN_CONFLICTED_FILES="src/file.ts" \
~/.local/bin/resolve-conflicts
```

### PATH Not Found

```bash
# Ensure ~/.local/bin is in PATH
echo $PATH | grep "\.local/bin"

# If missing, add to shell profile
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

### API Key Not Working

```bash
# Verify key is set
echo $ANTHROPIC_API_KEY
# Should print your key, not empty

# Test API directly
curl https://api.anthropic.com/v1/messages \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01" \
  -H "content-type: application/json" \
  -d "{\"model\": \"claude-opus-4-1\", \"max_tokens\": 100, \"messages\": [{\"role\": \"user\", \"content\": \"hi\"}]}"
```

### Resolver Runs Forever

Stackman waits for the resolver indefinitely — there is no timeout, so a hung
resolver blocks the sync until you interrupt it (Ctrl-C). If yours is slow or
hanging:

1. **Check for hangs** — Ensure resolver doesn't wait for interactive input
2. **Optimize the resolver** — Reduce token count, simplify prompt
3. **Increase model performance** — Use a faster model for initial tests
4. **Use caching** — Cache resolved patterns from previous conflicts
5. **Enforce your own limit** — Wrap the resolver in `timeout 600 <cmd>` if you
   want one

## Performance Tips

1. **Keep prompts concise** — Shorter prompts = faster responses
2. **Use temperature 0.3 or lower** — Reduces randomness, faster convergence
3. **Set max_tokens appropriately** — Don't ask for 4096 tokens if 1024 suffices
4. **Cache the template** — Store `stackman show-resolver-prompt` output once
5. **Monitor token usage** — Check API logs to optimize

## See Also

- [Conflict Resolution Guide](./conflict-resolution-guide.md)
- `stackman sync --help`
- `stackman show-resolver-prompt`
