from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .context import AppContext
from .git_ops import (
    get_git_config,
    get_pr_number,
    is_ancestor,
    rebase_in_progress,
    worktree_dirty_preview,
)
from .models import ConflictResolutionResult
from .resolver_prompt import get_default_prompt


def _emit(ctx: AppContext, message: str) -> None:
    """Write a progress/narration line to stdout, ensuring a trailing newline + flush."""
    ctx.stdout.write(message)
    if not message.endswith("\n"):
        ctx.stdout.write("\n")
    ctx.stdout.flush()


@dataclass
class RebaseConflictContext:
    """Context for a rebase conflict."""

    branch_name: str
    branch_wt: Path
    parent_name: str
    parent_tip: str
    fork_point: str


class RebaseConflictValidator:
    """Validates the state of an in-progress rebase.

    Centralizes all rebase-state checks (is it complete? is the tree clean?)
    so the same logic is used in both interactive and resolver-based paths.
    """

    def __init__(self, branch_wt: Path, parent_tip: str):
        self.branch_wt = branch_wt
        self.parent_tip = parent_tip

    def is_rebase_in_progress(self) -> bool:
        """Check if a rebase is still in progress."""
        return rebase_in_progress(self.branch_wt)

    def is_rebase_complete(self) -> bool:
        """Check if the rebase has completed successfully.

        Returns True if HEAD is an ancestor of parent_tip (the rebase target),
        meaning all commits have been replayed.
        """
        return is_ancestor(self.branch_wt, self.parent_tip, "HEAD")

    def working_tree_status(self) -> str | None:
        """Get a preview of uncommitted changes in the working tree.

        Returns None if the tree is clean, otherwise a string showing the changes.
        """
        return worktree_dirty_preview(self.branch_wt)

    def is_working_tree_clean(self) -> bool:
        """Check if the working tree has no uncommitted changes."""
        return self.working_tree_status() is None

    def validate_rebase_success(self) -> tuple[bool, str | None]:
        """Validate that a rebase that reported success (exit 0) actually succeeded.

        Returns (success: bool, error_message: str | None).
        On success, returns (True, None).
        On failure, returns (False, error_message) explaining what's wrong.
        """
        if self.is_rebase_in_progress():
            return False, "Rebase is still in progress (git rebase --continue needed)"

        if not self.is_rebase_complete():
            return False, "Rebase did not complete (HEAD is not at the target commit)"

        if not self.is_working_tree_clean():
            return False, "Working tree has uncommitted changes"

        return True, None


class RebaseConflictResolution:
    """Orchestrates conflict resolution during a rebase.

    Owns the decision logic for choosing between interactive and resolver-based
    conflict resolution, environment preparation, and result validation.
    """

    def __init__(
        self,
        ctx: AppContext,
        conflict_ctx: RebaseConflictContext,
        resolver: str | None = None,
        no_wait: bool = False,
    ):
        self.ctx = ctx
        self.conflict_ctx = conflict_ctx
        self.resolver = resolver
        self.no_wait = no_wait
        self.validator = RebaseConflictValidator(conflict_ctx.branch_wt, conflict_ctx.parent_tip)

    def resolve(self) -> ConflictResolutionResult:
        """Attempt to resolve the conflict using the appropriate strategy.

        Returns ConflictResolutionResult with status in ["success", "failure", "needs_manual"].
        Priority: resolver > interactive > fail
        """
        _emit(self.ctx, f"[stackman] Rebase conflict on {self.conflict_ctx.branch_name!r}.")

        # Try resolver path if configured (prioritize over interactive)
        if self.resolver:
            return self._try_resolver()

        # Try interactive path if available and not forced to non-interactive
        if self._should_try_interactive():
            return self._try_interactive()

        # No resolver and can't be interactive — fail with clear message
        self.ctx.stderr.write(
            "[stackman] Conflict resolution required but in non-interactive mode.\n"
            "[stackman] Use --resolver <cmd> to enable non-interactive conflict resolution, "
            "or resolve manually and run `git rebase --continue`, then `stackman sync` again.\n"
        )
        return ConflictResolutionResult(
            status="failure",
            message="Conflict resolution required but in non-interactive mode (no resolver configured)",
        )

    def _should_try_interactive(self) -> bool:
        """Check if interactive mode is available and desired."""
        return not self.no_wait and hasattr(self.ctx.stdin, "readline")

    def _try_interactive(self) -> ConflictResolutionResult:
        """Try interactive conflict resolution via stdin prompts."""
        while True:
            self.ctx.stdout.write(
                "[stackman] Resolve conflicts, run `git rebase --continue` or `git rebase --abort`, "
                "then press Enter to resume.\n"
            )
            self.ctx.stdout.flush()
            line = self.ctx.stdin.readline()
            if line == "":
                self.ctx.stderr.write(
                    "[stackman] Input closed while waiting for rebase resolution.\n"
                )
                return ConflictResolutionResult(
                    status="failure",
                    message="Input closed while waiting for rebase resolution",
                )
            if self.validator.is_rebase_in_progress():
                _emit(
                    self.ctx,
                    f"[stackman] Rebase on {self.conflict_ctx.branch_name!r} is still in progress.",
                )
                continue
            if self.validator.is_rebase_complete():
                _emit(
                    self.ctx,
                    f"[stackman] Rebase on {self.conflict_ctx.branch_name!r} completed; resuming sync.",
                )
                return ConflictResolutionResult(
                    status="success",
                    message="Rebase completed successfully (interactive)",
                )
            self.ctx.stderr.write(
                f"[stackman] Rebase on {self.conflict_ctx.branch_name!r} was aborted.\n"
            )
            return ConflictResolutionResult(
                status="needs_manual",
                message="Rebase was aborted by user",
            )

    def _try_resolver(self) -> ConflictResolutionResult:
        """Try resolver-based conflict resolution via subprocess."""
        resolver = self.resolver
        if resolver is None:
            raise RuntimeError("resolver path selected without a resolver command")
        return _invoke_resolver(self.ctx, self.conflict_ctx, resolver)


def resolve_rebase_conflict(
    ctx: AppContext,
    conflict_ctx: RebaseConflictContext,
    resolver: str | None = None,
    no_wait: bool = False,
) -> ConflictResolutionResult:
    """
    Resolve a rebase conflict, either interactively or via a resolver command.

    Backwards-compatibility shim: wraps RebaseConflictResolution class.
    Returns ConflictResolutionResult with status in ["success", "failure", "needs_manual"].
    """
    resolution = RebaseConflictResolution(ctx, conflict_ctx, resolver, no_wait)
    return resolution.resolve()


# Old implementation removed — now handled by RebaseConflictResolution class


def _invoke_resolver(
    ctx: AppContext,
    conflict_ctx: RebaseConflictContext,
    resolver: str,
) -> ConflictResolutionResult:
    """Invoke the resolver command and check the result."""
    _emit(ctx, f"[stackman] Invoking resolver: {resolver}")

    # Get conflicted files
    conflicted_files = _get_conflicted_files(conflict_ctx.branch_wt)

    # Populate env vars
    env_vars = _populate_resolver_env_vars(
        conflict_ctx.branch_wt,
        conflict_ctx.branch_name,
        conflict_ctx.parent_name,
        conflict_ctx.parent_tip,
        conflict_ctx.fork_point,
        conflicted_files,
    )

    # Expand @prompt to a temporary file containing the default conflict resolution prompt
    prompt_file = None
    if "@prompt" in resolver:
        try:
            # Write prompt to a temporary file
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
                prefix="stackman-prompt-",
            ) as f:
                f.write(get_default_prompt())
                prompt_file = f.name
            # Replace @prompt with the file path
            resolver = resolver.replace("@prompt", prompt_file)
        except Exception as e:
            ctx.stderr.write(f"[stackman] Failed to create prompt file: {e}\n")
            return ConflictResolutionResult(
                status="failure",
                message=f"Failed to create prompt file: {e}",
            )

    # Parse resolver command
    try:
        resolver_argv = shlex.split(resolver)
    except ValueError as e:
        ctx.stderr.write(f"[stackman] Failed to parse resolver command: {e}\n")
        if prompt_file:
            with contextlib.suppress(OSError):
                os.unlink(prompt_file)
        return ConflictResolutionResult(
            status="failure",
            message=f"Failed to parse resolver command: {e}",
        )

    if not resolver_argv:
        ctx.stderr.write("[stackman] Resolver command is empty.\n")
        if prompt_file:
            with contextlib.suppress(OSError):
                os.unlink(prompt_file)
        return ConflictResolutionResult(
            status="failure",
            message="Resolver command is empty",
        )

    # Prepare environment: inherit current env, add resolver vars
    resolver_env = os.environ.copy()
    resolver_env.update(env_vars)

    try:
        # Run resolver with stdin=/dev/null (no timeout)
        try:
            resolver_result = subprocess.run(
                resolver_argv,
                cwd=conflict_ctx.branch_wt,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                env=resolver_env,
            )
        except Exception as e:
            ctx.stderr.write(f"[stackman] Resolver invocation failed: {e}\n")
            _abort_rebase(conflict_ctx.branch_wt)
            return ConflictResolutionResult(
                status="failure",
                message=f"Resolver invocation failed: {e}",
            )

        # Check resolver outcome
        resolver_output = ""
        if resolver_result.stdout:
            resolver_output += resolver_result.stdout
            ctx.stdout.write(resolver_result.stdout)
        if resolver_result.stderr:
            resolver_output += resolver_result.stderr
            ctx.stderr.write(resolver_result.stderr)

        if resolver_result.returncode != 0:
            ctx.stderr.write(
                f"[stackman] Resolver failed: exit code {resolver_result.returncode}\n"
            )
            _abort_rebase(conflict_ctx.branch_wt)
            return ConflictResolutionResult(
                status="failure",
                message=f"Resolver exited with code {resolver_result.returncode}",
                resolver_output=resolver_output if resolver_output else None,
            )

        # Validate that the rebase actually succeeded (check end state)
        validator = RebaseConflictValidator(conflict_ctx.branch_wt, conflict_ctx.parent_tip)
        success, error_msg = validator.validate_rebase_success()
        if not success:
            ctx.stderr.write(f"[stackman] Resolver exited successfully but {error_msg}.\n")
            _abort_rebase(conflict_ctx.branch_wt)
            return ConflictResolutionResult(
                status="failure",
                message=f"Resolver exited successfully but {error_msg}",
                resolver_output=resolver_output if resolver_output else None,
            )

        _emit(ctx, "[stackman] Resolver completed successfully; resuming sync.")
        return ConflictResolutionResult(
            status="success",
            message="Resolver completed successfully",
            resolver_output=resolver_output if resolver_output else None,
        )
    finally:
        # Clean up prompt file if it was created
        if prompt_file:
            with contextlib.suppress(OSError):
                os.unlink(prompt_file)


def _populate_resolver_env_vars(
    branch_wt: Path,
    branch_name: str,
    parent_name: str,
    parent_tip: str,
    fork_point: str,
    conflicted_files: list[str],
) -> dict[str, str]:
    """Build environment variables for resolver invocation."""
    env_vars = {
        "STACKMAN_BRANCH": branch_name,
        "STACKMAN_PARENT": parent_name,
        "STACKMAN_PARENT_TIP": parent_tip,
        "STACKMAN_FORK_POINT": fork_point,
        "STACKMAN_CONFLICTED_FILES": "\n".join(conflicted_files),
        "STACKMAN_OPERATION": "rebase",
    }

    # Optional: auto-discovered values
    repo_url = get_git_config(branch_wt, "remote.origin.url")
    if repo_url:
        env_vars["STACKMAN_REPO_URL"] = repo_url

    parent_pr = get_pr_number(branch_wt, parent_name)
    if parent_pr is not None:
        env_vars["STACKMAN_PARENT_PR_NUMBER"] = str(parent_pr)

    branch_pr = get_pr_number(branch_wt, branch_name)
    if branch_pr is not None:
        env_vars["STACKMAN_PR_NUMBER"] = str(branch_pr)

    return env_vars


def _get_conflicted_files(cwd: Path) -> list[str]:
    """Get list of conflicted files from git status."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []

    conflicted = []
    for line in result.stdout.splitlines():
        # Git merge conflict codes (git status --porcelain during rebase):
        # UU = both modified, UD = deleted by them, DU = deleted by us,
        # DD = both deleted, AU = added by them, UA = added by us, AA = both added
        if line and line[0:2] in ("UU", "UD", "DU", "DD", "AU", "UA", "AA"):
            path = line[3:].strip()
            if path:
                conflicted.append(path)
    return conflicted


def _abort_rebase(cwd: Path) -> None:
    """Abort an in-progress rebase."""
    subprocess.run(
        ["git", "rebase", "--abort"],
        cwd=cwd,
        check=False,
        capture_output=True,
    )
