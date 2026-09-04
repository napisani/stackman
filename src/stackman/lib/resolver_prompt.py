"""Opinionated conflict resolution prompt for stackman resolvers.

Provides a default, extensible prompt that inlines conflict resolution
methodology with clear safety guardrails and exit instructions.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

# The default conflict resolution prompt template.
# All environment variables are templated as {VAR_NAME} for easy substitution.
DEFAULT_CONFLICT_RESOLUTION_PROMPT = """You are resolving merge conflicts during an unattended Git rebase.

## Context
Branch being rebased: {STACKMAN_BRANCH}
Parent branch: {STACKMAN_PARENT}
Parent branch tip: {STACKMAN_PARENT_TIP}
Fork point (rebase upstream): {STACKMAN_FORK_POINT}
Conflicted files:
{STACKMAN_CONFLICTED_FILES}

## Optional Context
Repository: {STACKMAN_REPO_URL}
Parent branch PR: {STACKMAN_PARENT_PR_NUMBER}
Current branch PR: {STACKMAN_PR_NUMBER}
Operation: {STACKMAN_OPERATION}

## Your Task: Resolve All Conflicts

For EACH conflicted file:

1. **Read the conflict markers** (<<<<<<, ======, >>>>>>>)
   - Understand what each side is trying to do
   - Read the code context to grasp both intents

2. **Merge intentionally**
   - Preserve both intents when safe (e.g., non-overlapping additions)
   - Choose the better version when sides conflict (e.g., one is refactored)
   - If merging them breaks functionality, ABORT (see below)

3. **Remove conflict markers**
   - Delete all <<<<<<, ======, >>>>>>>> markers
   - Result should be clean, valid code with no merge scars

4. **Validate syntax**
   - File should parse (run compiler check if possible)
   - No broken code, imports, or references

5. **Stage the file**
   - Run: git add <file>

6. **After all files are resolved**
   - Run: git rebase --continue
   - Rebase should complete cleanly with no further conflicts

## Exit Criteria

### Success (exit 0)
- All conflicts resolved intentionally
- All files staged with git add
- Rebase continued and completed
- No uncommitted changes remain
- Working tree is clean

### Failure (exit 1) — ABORT if:
- Any conflict is ambiguous (can't determine correct resolution)
- Conflict involves changes you don't fully understand
- Merging both sides would break code functionality
- Resolving would lose important logic from either side
- You're unsure about the safety of the merge
- Code won't compile/pass syntax check after resolution

## Safety First: When in Doubt, ABORT

You are resolving conflicts unattended. Better to fail safely than to guess wrong and break the codebase.

If a conflict seems risky or unclear:
1. Run: git rebase --abort
2. Exit with code 1
3. The user will manually review and resolve

## Termination

**On Success:**
```bash
git rebase --continue
# (wait for rebase to complete)
exit 0
```

**On Failure (unsafe):**
```bash
git rebase --abort
exit 1
```

## Common Patterns

### Both sides add the same import
→ Keep only one copy
→ Safe: non-overlapping additions

### Both sides modify the same function
→ Understand each modification
→ Safe if changes are compatible
→ Unsafe if they conflict functionally

### One side deletes, one side modifies
→ Understand why it was deleted
→ If deletion is intentional, keep it
→ If modification is necessary, restore and modify
→ Unsafe if restoration/modification breaks intent

### Conflicting logic changes
→ Read the git history/comments
→ Choose the version that's correct in context
→ If neither is fully correct, ABORT (unsafe)

Remember: Intent matters. You're merging two branches' work, not just text.
"""


def get_default_prompt(env: Mapping[str, str] | None = None) -> str:
    """Get the default conflict resolution prompt with context variables substituted.

    `env` is the mapping of STACKMAN_* values to substitute; it defaults to the
    process environment. Callers that build the resolver environment themselves
    (see `conflict_resolver._populate_resolver_env_vars`) must pass it, since
    those values are never written into `os.environ`.

    Required variables that are missing are left as `{VAR_NAME}`; optional ones
    render as "not provided".
    """
    prompt = DEFAULT_CONFLICT_RESOLUTION_PROMPT
    source = os.environ if env is None else env

    # Template all STACKMAN_* variables
    template_vars = {
        "STACKMAN_BRANCH": source.get("STACKMAN_BRANCH", "{STACKMAN_BRANCH}"),
        "STACKMAN_PARENT": source.get("STACKMAN_PARENT", "{STACKMAN_PARENT}"),
        "STACKMAN_PARENT_TIP": source.get("STACKMAN_PARENT_TIP", "{STACKMAN_PARENT_TIP}"),
        "STACKMAN_FORK_POINT": source.get("STACKMAN_FORK_POINT", "{STACKMAN_FORK_POINT}"),
        "STACKMAN_CONFLICTED_FILES": source.get(
            "STACKMAN_CONFLICTED_FILES", "{STACKMAN_CONFLICTED_FILES}"
        ),
        "STACKMAN_OPERATION": source.get("STACKMAN_OPERATION", "{STACKMAN_OPERATION}"),
        "STACKMAN_REPO_URL": source.get("STACKMAN_REPO_URL", "not provided"),
        "STACKMAN_PARENT_PR_NUMBER": source.get("STACKMAN_PARENT_PR_NUMBER", "not provided"),
        "STACKMAN_PR_NUMBER": source.get("STACKMAN_PR_NUMBER", "not provided"),
    }

    # Replace all template variables
    for key, value in template_vars.items():
        prompt = prompt.replace(f"{{{key}}}", value)

    return prompt


def get_template() -> str:
    """Get the raw prompt template with variable placeholders.

    Useful for users who want to see the template structure or extend it.
    """
    return DEFAULT_CONFLICT_RESOLUTION_PROMPT
