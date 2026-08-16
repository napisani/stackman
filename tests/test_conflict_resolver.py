"""Unit tests for conflict_resolver module, focusing on RebaseConflictValidator and RebaseConflictResolution."""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

from stackman.conflict_resolver import RebaseConflictResolution, RebaseConflictValidator
from stackman.context import AppContext
from stackman.models import ConflictResolutionResult


class TestRebaseConflictValidator:
    """Test the RebaseConflictValidator state checks."""

    def test_is_rebase_in_progress_true(self) -> None:
        """Test detecting when rebase is still in progress."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with patch("stackman.conflict_resolver.rebase_in_progress", return_value=True):
            assert validator.is_rebase_in_progress() is True

    def test_is_rebase_in_progress_false(self) -> None:
        """Test detecting when rebase is not in progress."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with patch("stackman.conflict_resolver.rebase_in_progress", return_value=False):
            assert validator.is_rebase_in_progress() is False

    def test_is_rebase_complete_true(self) -> None:
        """Test detecting when rebase completed (HEAD at target)."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with patch("stackman.conflict_resolver.is_ancestor", return_value=True):
            assert validator.is_rebase_complete() is True

    def test_is_rebase_complete_false(self) -> None:
        """Test detecting when rebase did not complete."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with patch("stackman.conflict_resolver.is_ancestor", return_value=False):
            assert validator.is_rebase_complete() is False

    def test_working_tree_status_clean(self) -> None:
        """Test detecting clean working tree."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with patch("stackman.conflict_resolver.worktree_dirty_preview", return_value=None):
            assert validator.working_tree_status() is None
            assert validator.is_working_tree_clean() is True

    def test_working_tree_status_dirty(self) -> None:
        """Test detecting dirty working tree."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")
        dirty_preview = " M file.txt"

        with patch("stackman.conflict_resolver.worktree_dirty_preview", return_value=dirty_preview):
            assert validator.working_tree_status() == dirty_preview
            assert validator.is_working_tree_clean() is False

    def test_validate_rebase_success_all_good(self) -> None:
        """Test successful rebase validation (all checks pass)."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with (
            patch("stackman.conflict_resolver.rebase_in_progress", return_value=False),
            patch("stackman.conflict_resolver.is_ancestor", return_value=True),
            patch("stackman.conflict_resolver.worktree_dirty_preview", return_value=None),
        ):
            success, error_msg = validator.validate_rebase_success()
            assert success is True
            assert error_msg is None

    def test_validate_rebase_success_still_in_progress(self) -> None:
        """Test validation fails when rebase is still in progress."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with patch("stackman.conflict_resolver.rebase_in_progress", return_value=True):
            success, error_msg = validator.validate_rebase_success()
            assert success is False
            assert "still in progress" in error_msg.lower()

    def test_validate_rebase_success_not_complete(self) -> None:
        """Test validation fails when rebase did not complete."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with (
            patch("stackman.conflict_resolver.rebase_in_progress", return_value=False),
            patch("stackman.conflict_resolver.is_ancestor", return_value=False),
        ):
            success, error_msg = validator.validate_rebase_success()
            assert success is False
            assert (
                "did not complete" in error_msg.lower() or "not at the target" in error_msg.lower()
            )

    def test_validate_rebase_success_dirty_tree(self) -> None:
        """Test validation fails when working tree has uncommitted changes."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        with (
            patch("stackman.conflict_resolver.rebase_in_progress", return_value=False),
            patch("stackman.conflict_resolver.is_ancestor", return_value=True),
            patch("stackman.conflict_resolver.worktree_dirty_preview", return_value=" M file.txt"),
        ):
            success, error_msg = validator.validate_rebase_success()
            assert success is False
            assert "uncommitted" in error_msg.lower()

    def test_validate_rebase_success_short_circuit(self) -> None:
        """Test that validation stops at first failure (short-circuit)."""
        validator = RebaseConflictValidator(Path("/tmp/repo"), "abc123")

        # If rebase is still in progress, we don't need to check other conditions
        with patch("stackman.conflict_resolver.rebase_in_progress", return_value=True):
            success, error_msg = validator.validate_rebase_success()
            assert success is False
            # Error message should be about rebase in progress, not about tree state
            assert "still in progress" in error_msg.lower()


class TestRebaseConflictResolution:
    """Test the RebaseConflictResolution orchestrator class."""

    def _make_context(self) -> AppContext:
        """Create a test AppContext."""
        return AppContext(
            db_path=Path("/tmp/test.db"),
            cwd=Path("/tmp/repo"),
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    def test_should_try_interactive_when_available_and_not_no_wait(self) -> None:
        """Test interactive mode is selected when available and not forced to non-interactive."""
        ctx = self._make_context()
        ctx.stdin = io.StringIO("")  # Has readline method

        from stackman.conflict_resolver import RebaseConflictContext

        conflict_ctx = RebaseConflictContext(
            branch_name="feature",
            branch_wt=Path("/tmp/repo"),
            parent_name="main",
            parent_tip="abc123",
            fork_point="def456",
        )

        resolution = RebaseConflictResolution(ctx, conflict_ctx, resolver=None, no_wait=False)
        assert resolution._should_try_interactive() is True

    def test_should_not_try_interactive_when_no_wait(self) -> None:
        """Test interactive mode is skipped when no_wait=True."""
        ctx = self._make_context()
        ctx.stdin = io.StringIO("")

        from stackman.conflict_resolver import RebaseConflictContext

        conflict_ctx = RebaseConflictContext(
            branch_name="feature",
            branch_wt=Path("/tmp/repo"),
            parent_name="main",
            parent_tip="abc123",
            fork_point="def456",
        )

        resolution = RebaseConflictResolution(ctx, conflict_ctx, resolver=None, no_wait=True)
        assert resolution._should_try_interactive() is False

    def test_should_not_try_interactive_when_no_stdin(self) -> None:
        """Test interactive mode is skipped when stdin has no readline."""
        ctx = self._make_context()
        ctx.stdin = None  # No readline method

        from stackman.conflict_resolver import RebaseConflictContext

        conflict_ctx = RebaseConflictContext(
            branch_name="feature",
            branch_wt=Path("/tmp/repo"),
            parent_name="main",
            parent_tip="abc123",
            fork_point="def456",
        )

        resolution = RebaseConflictResolution(ctx, conflict_ctx, resolver=None, no_wait=False)
        assert resolution._should_try_interactive() is False

    def test_resolve_without_resolver_and_no_interactive_fails(self) -> None:
        """Test that resolve fails with clear message when no resolver and can't be interactive."""
        ctx = self._make_context()
        ctx.stdin = None  # No stdin

        from stackman.conflict_resolver import RebaseConflictContext

        conflict_ctx = RebaseConflictContext(
            branch_name="feature",
            branch_wt=Path("/tmp/repo"),
            parent_name="main",
            parent_tip="abc123",
            fork_point="def456",
        )

        resolution = RebaseConflictResolution(ctx, conflict_ctx, resolver=None, no_wait=False)
        result = resolution.resolve()

        assert result.status == "failure"
        assert "non-interactive mode" in result.message.lower()
        assert "no resolver" in result.message.lower()

    def test_try_interactive_success_when_rebase_complete(self) -> None:
        """Test interactive path succeeds when user resolves and rebase completes."""
        ctx = self._make_context()
        ctx.stdin = io.StringIO("\n")  # User just presses Enter

        from stackman.conflict_resolver import RebaseConflictContext

        conflict_ctx = RebaseConflictContext(
            branch_name="feature",
            branch_wt=Path("/tmp/repo"),
            parent_name="main",
            parent_tip="abc123",
            fork_point="def456",
        )

        with (
            patch("stackman.conflict_resolver.rebase_in_progress", return_value=False),
            patch("stackman.conflict_resolver.is_ancestor", return_value=True),
            patch("stackman.conflict_resolver.worktree_dirty_preview", return_value=None),
        ):
            resolution = RebaseConflictResolution(ctx, conflict_ctx, resolver=None, no_wait=False)
            result = resolution._try_interactive()

            assert result.status == "success"
            assert "completed successfully" in result.message.lower()

    def test_try_interactive_needs_manual_when_rebase_aborted(self) -> None:
        """Test interactive path returns needs_manual when user aborts."""
        ctx = self._make_context()
        ctx.stdin = io.StringIO("\n")  # User presses Enter

        from stackman.conflict_resolver import RebaseConflictContext

        conflict_ctx = RebaseConflictContext(
            branch_name="feature",
            branch_wt=Path("/tmp/repo"),
            parent_name="main",
            parent_tip="abc123",
            fork_point="def456",
        )

        with (
            patch("stackman.conflict_resolver.rebase_in_progress", return_value=False),
            patch("stackman.conflict_resolver.is_ancestor", return_value=False),
        ):
            resolution = RebaseConflictResolution(ctx, conflict_ctx, resolver=None, no_wait=False)
            result = resolution._try_interactive()

            assert result.status == "needs_manual"
            assert "aborted" in result.message.lower()

    def test_resolver_prioritized_over_interactive(self) -> None:
        """Test that resolver is used even when interactive mode is available."""
        ctx = self._make_context()
        ctx.stdin = io.StringIO("\n")  # stdin available (would enable interactive)

        from stackman.conflict_resolver import RebaseConflictContext

        conflict_ctx = RebaseConflictContext(
            branch_name="feature",
            branch_wt=Path("/tmp/repo"),
            parent_name="main",
            parent_tip="abc123",
            fork_point="def456",
        )

        # Mock the resolver to succeed
        with (
            patch("stackman.conflict_resolver.rebase_in_progress", return_value=False),
            patch("stackman.conflict_resolver.is_ancestor", return_value=True),
            patch("stackman.conflict_resolver.worktree_dirty_preview", return_value=None),
            patch("stackman.conflict_resolver._invoke_resolver") as mock_invoke,
        ):
            mock_invoke.return_value = ConflictResolutionResult(
                status="success",
                message="Resolved by resolver",
            )

            # Create resolution with both resolver and interactive stdin available
            resolution = RebaseConflictResolution(
                ctx,
                conflict_ctx,
                resolver="claude -p @prompt",  # resolver provided
                no_wait=False,  # interactive would be available
            )
            result = resolution.resolve()

            # Verify resolver was called (not interactive)
            mock_invoke.assert_called_once()
            assert result.status == "success"
            # Verify stdin was not read (would indicate interactive was used)
            assert ctx.stdin.getvalue() == "\n"  # stdin unchanged
