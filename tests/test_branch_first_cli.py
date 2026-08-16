from __future__ import annotations

import io
import json

from click.testing import CliRunner

from stackman.app import StackmanApp
from stackman.cli import cli
from stackman.store import get_branch, initialize, label_branch, upsert_branch


def _track_ab(git_repo, stackman_db_path):
    """Track a two-branch stack a→main, b→a and return the repo key."""
    git_repo.checkout_new("a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout_new("b", from_ref="a")
    git_repo.commit("b", filename="b.txt", content="b\n")
    git_repo.checkout("main")
    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="a",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("a", "main"),
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="b",
        parent_branch_name="a",
        fork_point_sha=git_repo.merge_base("b", "a"),
    )
    return repo_key


class _FakeCtx:
    def __init__(self, params, parent=None):
        self.params = params
        self.parent = parent


def test_completion_lists_tracked_branches(git_repo, stackman_db_path) -> None:
    from stackman.cli import _complete_tracked_branches

    _track_ab(git_repo, stackman_db_path)
    ctx = _FakeCtx({"db_path": stackman_db_path, "repo_path": git_repo.root})

    assert set(_complete_tracked_branches(ctx, None, "")) == {"a", "b"}
    assert _complete_tracked_branches(ctx, None, "a") == ["a"]


def test_completion_never_crashes_on_missing_db(git_repo, stackman_db_path, tmp_path) -> None:
    from stackman.cli import _complete_tracked_branches

    missing = tmp_path / "does-not-exist.db"
    ctx = _FakeCtx({"db_path": missing, "repo_path": git_repo.root})

    assert _complete_tracked_branches(ctx, None, "") == []
    assert not missing.exists()  # completion must not create the db as a side effect


def test_global_options_work_after_subcommand(git_repo, stackman_db_path) -> None:
    _track_ab(git_repo, stackman_db_path)

    result = CliRunner().invoke(
        cli,
        ["list", "--db-path", str(stackman_db_path), "--repo", str(git_repo.root)],
    )

    assert result.exit_code == 0, result.output
    assert "└── b" in result.output


def test_status_reports_other_branch_without_checkout(git_repo, stackman_db_path) -> None:
    _track_ab(git_repo, stackman_db_path)  # 'main' is checked out

    result = CliRunner().invoke(
        cli,
        ["--db-path", str(stackman_db_path), "--repo", str(git_repo.root), "status", "b"],
    )

    assert result.exit_code == 0, result.output
    assert "branch: b" in result.output
    assert "parent: a" in result.output


def test_list_json_is_machine_readable(git_repo, stackman_db_path) -> None:
    _track_ab(git_repo, stackman_db_path)

    result = CliRunner().invoke(
        cli,
        ["--db-path", str(stackman_db_path), "--repo", str(git_repo.root), "list", "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    names = {b["branch"]: b["parent"] for b in payload["branches"]}
    assert names == {"a": "main", "b": "a"}


def test_status_json_untracked_branch_exits_zero(git_repo, stackman_db_path) -> None:
    _track_ab(git_repo, stackman_db_path)

    result = CliRunner().invoke(
        cli,
        [
            "--db-path",
            str(stackman_db_path),
            "--repo",
            str(git_repo.root),
            "status",
            "main",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["branch"] == "main"
    assert payload["tracked"] is False


def test_track_command_registers_named_branch_without_checking_it_out(
    git_repo, stackman_db_path
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    git_repo.checkout("main")

    result = CliRunner().invoke(
        cli,
        [
            "--db-path",
            str(stackman_db_path),
            "--repo",
            str(git_repo.root),
            "track",
            "feature",
            "--parent",
            "main",
        ],
    )

    assert result.exit_code == 0, result.output
    tracked = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "feature")
    assert tracked is not None
    assert tracked.parent_branch_name == "main"
    assert git_repo.current_branch() == "main"


def test_default_command_shows_current_branch_status(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")
    assert (
        StackmanApp(
            db_path=stackman_db_path,
            cwd=git_repo.root,
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        ).track(parent="main")
        == 0
    )

    result = CliRunner().invoke(
        cli,
        ["--db-path", str(stackman_db_path), "--repo", str(git_repo.root)],
    )

    assert result.exit_code == 0, result.output
    assert "branch: feature" in result.output
    assert "parent: main" in result.output


def test_chain_command_registers_linear_stack(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout_new("b", from_ref="a")
    git_repo.commit("b", filename="b.txt", content="b\n")
    git_repo.checkout("main")

    result = CliRunner().invoke(
        cli,
        [
            "--db-path",
            str(stackman_db_path),
            "--repo",
            str(git_repo.root),
            "chain",
            "main",
            "a",
            "b",
        ],
    )

    assert result.exit_code == 0, result.output
    a = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "a")
    b = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "b")
    assert a is not None and a.parent_branch_name == "main"
    assert b is not None and b.parent_branch_name == "a"


def test_sync_named_branch_from_main_syncs_full_stack(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout_new("b", from_ref="a")
    git_repo.commit("b", filename="b.txt", content="b\n")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="a",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("a", "main"),
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="b",
        parent_branch_name="a",
        fork_point_sha=git_repo.merge_base("b", "a"),
    )
    label_branch(stackman_db_path, repo_key, "a", "stack-chain", anchor_branch_name="main")
    label_branch(stackman_db_path, repo_key, "b", "stack-chain", anchor_branch_name="main")

    git_repo.checkout("main")
    git_repo.commit("main moves", filename="main.txt", content="main\n")
    main_tip = git_repo.rev_parse("main")

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.sync(branch="b") == 0
    git_repo.checkout("a")
    assert git_repo.is_ancestor(main_tip, "HEAD")
    a_tip = git_repo.rev_parse("a")
    git_repo.checkout("b")
    assert git_repo.is_ancestor(a_tip, "HEAD")
    assert "Sync finished successfully" in stdout.getvalue()


def test_done_named_branch_from_main_reparents_children(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("topic", from_ref="main")
    git_repo.commit("topic", filename="topic.txt", content="topic\n")
    git_repo.checkout_new("child", from_ref="topic")
    git_repo.commit("child", filename="child.txt", content="child\n")
    git_repo.checkout("main")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="topic",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("topic", "main"),
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="child",
        parent_branch_name="topic",
        fork_point_sha=git_repo.merge_base("child", "topic"),
    )

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert app.done(branch="topic") == 0
    assert get_branch(stackman_db_path, repo_key, "topic") is None
    child = get_branch(stackman_db_path, repo_key, "child")
    assert child is not None
    assert child.parent_branch_name == "main"


def test_forget_named_branch_does_not_reparent_children(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("topic", from_ref="main")
    git_repo.commit("topic", filename="topic.txt", content="topic\n")
    git_repo.checkout_new("child", from_ref="topic")
    git_repo.commit("child", filename="child.txt", content="child\n")
    git_repo.checkout("main")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="topic",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("topic", "main"),
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="child",
        parent_branch_name="topic",
        fork_point_sha=git_repo.merge_base("child", "topic"),
    )

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert app.forget(branch="topic") == 0
    assert get_branch(stackman_db_path, repo_key, "topic") is None
    child = get_branch(stackman_db_path, repo_key, "child")
    assert child is not None
    assert child.parent_branch_name == "topic"


def test_forget_all_wipes_repo_tracking_with_yes(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout_new("b", from_ref="a")
    git_repo.commit("b", filename="b.txt", content="b\n")
    git_repo.checkout("main")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="a",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("a", "main"),
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="b",
        parent_branch_name="a",
        fork_point_sha=git_repo.merge_base("b", "a"),
    )

    result = CliRunner().invoke(
        cli,
        [
            "--db-path",
            str(stackman_db_path),
            "--repo",
            str(git_repo.root),
            "forget",
            "--all",
            "--yes",
        ],
    )

    assert result.exit_code == 0, result.output
    assert get_branch(stackman_db_path, repo_key, "a") is None
    assert get_branch(stackman_db_path, repo_key, "b") is None


class _TtyStringIO(io.StringIO):
    """A StringIO that claims to be a TTY, for exercising the interactive prompt path."""

    def isatty(self) -> bool:
        return True


def _seed_branch_a(git_repo, stackman_db_path) -> str:
    git_repo.checkout_new("a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout("main")
    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="a",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("a", "main"),
    )
    return repo_key


def test_forget_all_refuses_without_tty_or_yes(git_repo, stackman_db_path) -> None:
    repo_key = _seed_branch_a(git_repo, stackman_db_path)

    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),  # not a TTY
        stdout=io.StringIO(),
        stderr=stderr,
    )

    # Never blocks on a prompt when stdin is not a TTY; refuses with a nonzero exit.
    assert app.forget_all() == 1
    assert "not a TTY" in stderr.getvalue()
    assert get_branch(stackman_db_path, repo_key, "a") is not None


def test_forget_all_interactive_decline_preserves_tracking(git_repo, stackman_db_path) -> None:
    repo_key = _seed_branch_a(git_repo, stackman_db_path)

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=_TtyStringIO("n\n"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert app.forget_all() == 1
    assert get_branch(stackman_db_path, repo_key, "a") is not None


def test_forget_all_interactive_confirm_wipes(git_repo, stackman_db_path) -> None:
    repo_key = _seed_branch_a(git_repo, stackman_db_path)

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=_TtyStringIO("y\n"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert app.forget_all() == 0
    assert get_branch(stackman_db_path, repo_key, "a") is None


def test_forget_all_global_requires_full_yes_word(git_repo, stackman_db_path) -> None:
    repo_key = _seed_branch_a(git_repo, stackman_db_path)

    # A bare 'y' is not enough for the global wipe.
    declined = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=_TtyStringIO("y\n"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert declined.forget_all(is_global=True) == 1
    assert get_branch(stackman_db_path, repo_key, "a") is not None

    # The full word confirms.
    confirmed = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=_TtyStringIO("yes\n"),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert confirmed.forget_all(is_global=True) == 0
    assert get_branch(stackman_db_path, repo_key, "a") is None


def test_forget_all_dry_run_lists_without_deleting(git_repo, stackman_db_path) -> None:
    repo_key = _seed_branch_a(git_repo, stackman_db_path)

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    # Dry run needs no --yes and no TTY: it changes nothing.
    assert app.forget_all(dry_run=True) == 0
    out = stdout.getvalue()
    assert "Dry run" in out
    assert "- a (parent main)" in out
    assert get_branch(stackman_db_path, repo_key, "a") is not None


def test_forget_dry_run_requires_all(git_repo, stackman_db_path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "--db-path",
            str(stackman_db_path),
            "--repo",
            str(git_repo.root),
            "forget",
            "x",
            "--dry-run",
        ],
    )
    assert result.exit_code != 0
    assert "--dry-run only applies together with --all" in result.output


def test_forget_all_and_branch_are_mutually_exclusive(git_repo, stackman_db_path) -> None:
    result = CliRunner().invoke(
        cli,
        [
            "--db-path",
            str(stackman_db_path),
            "--repo",
            str(git_repo.root),
            "forget",
            "topic",
            "--all",
        ],
    )
    assert result.exit_code != 0
    assert "not both" in result.output


def test_list_command_is_repo_local_by_default(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature", filename="feature.txt", content="feature\n")
    git_repo.checkout("main")
    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("feature", "main"),
    )
    label_branch(stackman_db_path, repo_key, "feature", "stack-feature", anchor_branch_name="main")

    result = CliRunner().invoke(
        cli,
        ["--db-path", str(stackman_db_path), "--repo", str(git_repo.root), "list"],
    )

    assert result.exit_code == 0, result.output
    assert "Tracked branches in" in result.output
    assert "feature" in result.output
    assert "stack-feature" not in result.output
