from __future__ import annotations

import io
import subprocess
from pathlib import Path

from stackman.app import StackmanApp
from stackman.git_ops import is_ancestor
from stackman.store import get_branch, initialize, label_branch, upsert_branch


class _ConflictResolverInput:
    def __init__(self, resolver) -> None:
        self._resolver = resolver
        self._calls = 0

    def readline(self) -> str:
        self._calls += 1
        self._resolver(self._calls)
        return "\n"


def _commit_subjects_in_range(git_repo, rev_range: str) -> list[str]:
    output = git_repo.git("log", "--reverse", "--format=%s", rev_range)
    return [line for line in output.splitlines() if line]


def _commit_count_in_range(git_repo, rev_range: str) -> int:
    return int(git_repo.git("rev-list", "--count", rev_range))


def _file_at(git_repo, ref: str, path: str) -> str:
    return git_repo.git("show", f"{ref}:{path}")


def _ancestry_from(git_repo, ref: str) -> list[str]:
    output = git_repo.git("rev-list", ref)
    return [line for line in output.splitlines() if line]


def test_sync_rebases_linear_stack_when_trunk_moves(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")

    fork = git_repo.merge_base("feature", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-1")

    git_repo.checkout("main")
    git_repo.commit("main moves", filename="main.txt", content="main\n")

    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    assert app.sync(branch="feature") == 0
    assert stderr.getvalue() == ""

    out = stdout.getvalue()
    assert "stack-1" not in out
    assert "feature" in out
    assert "Sync finished successfully" in out

    assert git_repo.current_branch() == "main"
    git_repo.checkout("feature")
    assert git_repo.is_ancestor(git_repo.rev_parse("main"), "HEAD")


def test_sync_dry_run_reports_anchor_and_uses_it_for_root_rebase(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("release_base", from_ref="main")
    git_repo.commit("release", filename="release.txt", content="release\n")
    git_repo.checkout_new("feature", from_ref="release_base")
    git_repo.commit("feature", filename="feature.txt", content="feature\n")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("feature", "main"),
    )
    label_branch(
        db_path,
        git_repo.canonical_repo_key(),
        "feature",
        "stack-anchor",
        anchor_branch_name="release_base",
    )

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.sync(branch="feature", dry_run=True) == 0
    out = stdout.getvalue()
    assert "[stackman] Anchor branch: 'release_base'" in out
    assert "feature: rebase onto tip of 'release_base'" in out


def test_sync_rebases_root_branch_onto_non_main_anchor(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("release_base", from_ref="main")
    git_repo.commit("release 1", filename="release.txt", content="release 1\n")
    git_repo.checkout_new("feature", from_ref="release_base")
    git_repo.commit("feature", filename="feature.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "release_base")

    git_repo.checkout("release_base")
    git_repo.commit("release 2", filename="release2.txt", content="release 2\n")
    release_tip = git_repo.rev_parse("release_base")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="release_base",
        fork_point_sha=fork,
    )
    label_branch(
        db_path,
        git_repo.canonical_repo_key(),
        "feature",
        "stack-anchor",
        anchor_branch_name="release_base",
    )

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert app.sync(branch="feature") == 0
    git_repo.checkout("feature")
    assert git_repo.merge_base("feature", "release_base") == release_tip


def test_sync_second_run_skips_branch_already_synced_to_parent_tip(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature work", filename="feature.txt", content="feature\n")

    fork = git_repo.merge_base("feature", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-1")

    git_repo.checkout("main")
    git_repo.commit("main moves", filename="main.txt", content="main\n")
    main_tip = git_repo.rev_parse("main")

    first_stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=first_stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature") == 0

    tracked = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "feature")
    assert tracked is not None
    assert tracked.fork_point_sha == main_tip

    git_repo.checkout("feature")
    before = git_repo.rev_parse("HEAD")
    git_repo.checkout("main")

    second_stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=second_stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature") == 0

    git_repo.checkout("feature")
    after = git_repo.rev_parse("HEAD")
    assert after == before
    assert "stored fork-point already matches current 'main' tip" in second_stdout.getvalue()


def test_sync_retains_post_fork_history_while_replacing_older_ancestry(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("branch1", from_ref="main")
    git_repo.commit("branch1 base", filename="branch1.txt", content="branch1 base\n")
    git_repo.checkout_new("branch2", from_ref="branch1")
    fork_branch2 = git_repo.merge_base("branch2", "branch1")
    git_repo.commit("branch2 commit 1", filename="branch2.txt", content="one\n")
    git_repo.commit("branch2 commit 2", filename="branch2.txt", content="two\n")

    expected_subjects = _commit_subjects_in_range(git_repo, f"{fork_branch2}..branch2")
    expected_count = _commit_count_in_range(git_repo, f"{fork_branch2}..branch2")

    initialize(stackman_db_path)
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch1",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("branch1", "main"),
    )
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch2",
        parent_branch_name="branch1",
        fork_point_sha=fork_branch2,
    )
    label_branch(stackman_db_path, git_repo.canonical_repo_key(), "branch1", "stack-history")

    git_repo.checkout("branch1")
    git_repo.commit("branch1 moves", filename="branch1.txt", content="branch1 moves\n")
    new_parent_tip = git_repo.rev_parse("branch1")
    expected_parent_ancestry = _ancestry_from(git_repo, new_parent_tip)

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert app.sync(branch="branch1") == 0

    tracked = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "branch2")
    assert tracked is not None
    assert tracked.fork_point_sha == new_parent_tip
    assert _commit_count_in_range(git_repo, f"{new_parent_tip}..branch2") == expected_count
    assert _commit_subjects_in_range(git_repo, f"{new_parent_tip}..branch2") == expected_subjects
    child_ancestry = _ancestry_from(git_repo, "branch2")
    preserved_count = _commit_count_in_range(git_repo, f"{new_parent_tip}..branch2")
    assert child_ancestry[preserved_count:] == expected_parent_ancestry


def test_sync_rebases_only_tail_branch_when_middle_branch_advances(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("branch1", from_ref="main")
    git_repo.commit("branch1 base", filename="branch1.txt", content="branch1 base\n")
    fork_branch1 = git_repo.merge_base("branch1", "main")

    git_repo.checkout_new("branch2", from_ref="branch1")
    fork_branch2 = git_repo.merge_base("branch2", "branch1")
    git_repo.commit("branch2 base", filename="branch2.txt", content="branch2 base\n")

    git_repo.checkout_new("branch3", from_ref="branch2")
    fork_branch3 = git_repo.merge_base("branch3", "branch2")
    git_repo.commit("branch3 commit 1", filename="branch3.txt", content="branch3 one\n")
    git_repo.commit("branch3 commit 2", filename="branch3.txt", content="branch3 two\n")

    branch1_tip_before_sync = git_repo.rev_parse("branch1")
    branch3_tip_before_sync = git_repo.rev_parse("branch3")
    branch3_subjects_before_sync = _commit_subjects_in_range(git_repo, f"{fork_branch3}..branch3")
    branch3_count_before_sync = _commit_count_in_range(git_repo, f"{fork_branch3}..branch3")

    initialize(stackman_db_path)
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch1",
        parent_branch_name="main",
        fork_point_sha=fork_branch1,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch2",
        parent_branch_name="branch1",
        fork_point_sha=fork_branch2,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch3",
        parent_branch_name="branch2",
        fork_point_sha=fork_branch3,
    )
    label_branch(
        stackman_db_path,
        git_repo.canonical_repo_key(),
        "branch1",
        "stack-middle-change",
    )

    git_repo.checkout("branch2")
    branch2_tip_before_manual_advance = git_repo.rev_parse("branch2")
    git_repo.commit("branch2 advances", filename="branch2.txt", content="branch2 advances\n")
    branch2_tip_after_manual_advance = git_repo.rev_parse("branch2")
    branch2_ancestry_after_manual_advance = _ancestry_from(
        git_repo, branch2_tip_after_manual_advance
    )
    git_repo.checkout("main")

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert app.sync(branch="branch1") == 0

    git_repo.checkout("branch1")
    branch1_tip_after_sync = git_repo.rev_parse("HEAD")
    git_repo.checkout("branch2")
    branch2_tip_after_sync = git_repo.rev_parse("HEAD")
    branch2_ancestry_after_sync = _ancestry_from(git_repo, "HEAD")
    git_repo.checkout("branch3")
    branch3_tip_after_sync = git_repo.rev_parse("HEAD")

    assert branch1_tip_after_sync == branch1_tip_before_sync
    assert branch2_tip_before_manual_advance != branch2_tip_after_manual_advance
    assert branch2_tip_after_sync == branch2_tip_after_manual_advance
    assert branch3_tip_after_sync != branch3_tip_before_sync
    assert (
        _commit_count_in_range(git_repo, f"{branch2_tip_after_manual_advance}..branch3")
        == branch3_count_before_sync
    )
    assert (
        _commit_subjects_in_range(git_repo, f"{branch2_tip_after_manual_advance}..branch3")
        == branch3_subjects_before_sync
    )
    branch3_ancestry_after_sync = _ancestry_from(git_repo, "HEAD")
    preserved_count = _commit_count_in_range(
        git_repo, f"{branch2_tip_after_manual_advance}..branch3"
    )
    assert branch3_ancestry_after_sync[preserved_count:] == branch2_ancestry_after_sync
    assert branch2_ancestry_after_sync == branch2_ancestry_after_manual_advance


def test_sync_rebases_descendants_after_root_branch_changes_then_skips_second_run(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("branch1", from_ref="main")
    git_repo.commit("branch1 base", filename="branch1.txt", content="branch1 base\n")
    fork_branch1 = git_repo.merge_base("branch1", "main")

    git_repo.checkout_new("branch2", from_ref="branch1")
    fork_branch2 = git_repo.merge_base("branch2", "branch1")
    git_repo.commit("branch2 commit 1", filename="branch2.txt", content="branch2 one\n")
    git_repo.commit("branch2 commit 2", filename="branch2.txt", content="branch2 two\n")

    git_repo.checkout_new("branch3", from_ref="branch2")
    fork_branch3 = git_repo.merge_base("branch3", "branch2")
    git_repo.commit("branch3 commit 1", filename="branch3.txt", content="branch3 one\n")
    git_repo.commit("branch3 commit 2", filename="branch3.txt", content="branch3 two\n")

    branch2_tip_before_sync = git_repo.rev_parse("branch2")
    branch3_tip_before_sync = git_repo.rev_parse("branch3")
    branch2_subjects_before_sync = _commit_subjects_in_range(git_repo, f"{fork_branch2}..branch2")
    branch2_count_before_sync = _commit_count_in_range(git_repo, f"{fork_branch2}..branch2")
    branch3_subjects_before_sync = _commit_subjects_in_range(git_repo, f"{fork_branch3}..branch3")
    branch3_count_before_sync = _commit_count_in_range(git_repo, f"{fork_branch3}..branch3")

    initialize(stackman_db_path)
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch1",
        parent_branch_name="main",
        fork_point_sha=fork_branch1,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch2",
        parent_branch_name="branch1",
        fork_point_sha=fork_branch2,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch3",
        parent_branch_name="branch2",
        fork_point_sha=fork_branch3,
    )
    label_branch(
        stackman_db_path,
        git_repo.canonical_repo_key(),
        "branch1",
        "stack-root-change",
    )

    git_repo.checkout("branch1")
    git_repo.commit(
        "branch1 manual advance",
        filename="branch1.txt",
        content="branch1 manual advance\n",
    )
    branch1_manual_new_tip = git_repo.rev_parse("branch1")
    branch1_ancestry_after_manual_advance = _ancestry_from(git_repo, "branch1")
    git_repo.checkout("main")

    first_stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=first_stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="branch1") == 0

    git_repo.checkout("branch1")
    branch1_tip_after_first_sync = git_repo.rev_parse("HEAD")
    git_repo.checkout("branch2")
    branch2_tip_after_first_sync = git_repo.rev_parse("HEAD")
    branch2_ancestry_after_first_sync = _ancestry_from(git_repo, "HEAD")
    git_repo.checkout("branch3")
    branch3_tip_after_first_sync = git_repo.rev_parse("HEAD")
    branch3_ancestry_after_first_sync = _ancestry_from(git_repo, "HEAD")

    assert branch1_tip_after_first_sync == branch1_manual_new_tip
    assert branch2_tip_after_first_sync != branch2_tip_before_sync
    assert branch3_tip_after_first_sync != branch3_tip_before_sync
    assert (
        _commit_count_in_range(git_repo, f"{branch1_manual_new_tip}..branch2")
        == branch2_count_before_sync
    )
    assert (
        _commit_subjects_in_range(git_repo, f"{branch1_manual_new_tip}..branch2")
        == branch2_subjects_before_sync
    )
    assert (
        _commit_count_in_range(git_repo, f"{branch2_tip_after_first_sync}..branch3")
        == branch3_count_before_sync
    )
    assert (
        _commit_subjects_in_range(git_repo, f"{branch2_tip_after_first_sync}..branch3")
        == branch3_subjects_before_sync
    )
    assert branch2_ancestry_after_first_sync[branch2_count_before_sync:] == (
        branch1_ancestry_after_manual_advance
    )
    assert branch3_ancestry_after_first_sync[branch3_count_before_sync:] == (
        branch2_ancestry_after_first_sync
    )

    git_repo.checkout("main")
    second_stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=second_stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="branch1") == 0

    git_repo.checkout("branch1")
    branch1_tip_after_second_sync = git_repo.rev_parse("HEAD")
    git_repo.checkout("branch2")
    branch2_tip_after_second_sync = git_repo.rev_parse("HEAD")
    git_repo.checkout("branch3")
    branch3_tip_after_second_sync = git_repo.rev_parse("HEAD")

    assert branch1_tip_after_second_sync == branch1_tip_after_first_sync
    assert branch2_tip_after_second_sync == branch2_tip_after_first_sync
    assert branch3_tip_after_second_sync == branch3_tip_after_first_sync
    assert (
        f"[stackman]   Skipping 'branch2'; stored fork-point already matches current "
        f"'branch1' tip {branch1_tip_after_first_sync[:7]}" in second_stdout.getvalue()
    )
    assert (
        f"[stackman]   Skipping 'branch3'; stored fork-point already matches current "
        f"'branch2' tip {branch2_tip_after_first_sync[:7]}" in second_stdout.getvalue()
    )


def test_sync_squash_collapses_multiple_post_fork_commits(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("first feature commit", filename="f1.txt", content="one\n")
    fork = git_repo.merge_base("feature", "main")
    git_repo.commit("second feature commit", filename="f2.txt", content="two\n")
    git_repo.commit("third feature commit", filename="f3.txt", content="three\n")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-squash")

    before_commits = git_repo.git("rev-list", "--count", f"{fork}..feature")
    assert before_commits == "3"

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature", squash=True) == 0

    after_commits = git_repo.git("rev-list", "--count", f"{fork}..feature")
    assert after_commits == "1"
    message = git_repo.git("log", "-1", "--format=%B", "feature").strip()
    assert message == "first feature commit"
    assert "collapsing 3 post-fork commits into one" in stdout.getvalue()


def test_sync_squash_leaves_single_post_fork_commit_unchanged(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("only feature commit", filename="f1.txt", content="one\n")
    fork = git_repo.merge_base("feature", "main")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-one")

    before = git_repo.rev_parse("feature")
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature", squash=True) == 0

    after = git_repo.rev_parse("feature")
    assert after == before
    assert "Squash skipped for 'feature' (1 post-fork commit)" in stdout.getvalue()


def test_sync_dry_run_reports_squash_plan(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("first feature commit", filename="f1.txt", content="one\n")
    fork = git_repo.merge_base("feature", "main")
    git_repo.commit("second feature commit", filename="f2.txt", content="two\n")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-dry-squash")

    before = git_repo.rev_parse("feature")
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature", dry_run=True, squash=True) == 0

    after = git_repo.rev_parse("feature")
    assert after == before
    out = stdout.getvalue()
    assert "optional squash" in out
    assert "would collapse 2 post-fork commits into one before rebasing" in out


def test_sync_squash_preserves_code_changes_across_entire_stack(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("foo", from_ref="main")
    git_repo.commit(
        "foo public api",
        filename="foo.py",
        content="def foo():\n    return 'foo'\n",
    )
    git_repo.commit(
        "foo helper",
        filename="foo_helper.py",
        content="def foo_helper():\n    return 'foo helper'\n",
    )
    foo_fork = git_repo.merge_base("foo", "main")

    git_repo.checkout_new("bar", from_ref="foo")
    git_repo.commit(
        "bar public api",
        filename="bar.py",
        content="def bar():\n    return 'bar'\n",
    )
    git_repo.commit(
        "bar helper",
        filename="bar_helper.py",
        content="def bar_helper():\n    return 'bar helper'\n",
    )
    bar_fork = git_repo.merge_base("bar", "foo")

    git_repo.checkout_new("zep", from_ref="bar")
    git_repo.commit(
        "zep public api",
        filename="zep.py",
        content="def zep():\n    return 'zep'\n",
    )
    git_repo.commit(
        "zep helper",
        filename="zep_helper.py",
        content="def zep_helper():\n    return 'zep helper'\n",
    )
    zep_fork = git_repo.merge_base("zep", "bar")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="foo",
        parent_branch_name="main",
        fork_point_sha=foo_fork,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="bar",
        parent_branch_name="foo",
        fork_point_sha=bar_fork,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="zep",
        parent_branch_name="bar",
        fork_point_sha=zep_fork,
    )
    label_branch(stackman_db_path, repo_key, "foo", "stack-squash-code")

    git_repo.checkout("main")
    git_repo.commit(
        "main dependency update",
        filename="requirements.txt",
        content="click==8.1.8\n",
    )

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="foo", squash=True) == 0

    assert _file_at(git_repo, "foo", "requirements.txt") == "click==8.1.8"
    assert _file_at(git_repo, "foo", "foo.py") == "def foo():\n    return 'foo'"
    assert _file_at(git_repo, "foo", "foo_helper.py") == (
        "def foo_helper():\n    return 'foo helper'"
    )

    assert _file_at(git_repo, "bar", "requirements.txt") == "click==8.1.8"
    assert _file_at(git_repo, "bar", "foo.py") == "def foo():\n    return 'foo'"
    assert _file_at(git_repo, "bar", "foo_helper.py") == (
        "def foo_helper():\n    return 'foo helper'"
    )
    assert _file_at(git_repo, "bar", "bar.py") == "def bar():\n    return 'bar'"
    assert _file_at(git_repo, "bar", "bar_helper.py") == (
        "def bar_helper():\n    return 'bar helper'"
    )

    assert _file_at(git_repo, "zep", "requirements.txt") == "click==8.1.8"
    assert _file_at(git_repo, "zep", "foo.py") == "def foo():\n    return 'foo'"
    assert _file_at(git_repo, "zep", "foo_helper.py") == (
        "def foo_helper():\n    return 'foo helper'"
    )
    assert _file_at(git_repo, "zep", "bar.py") == "def bar():\n    return 'bar'"
    assert _file_at(git_repo, "zep", "bar_helper.py") == (
        "def bar_helper():\n    return 'bar helper'"
    )
    assert _file_at(git_repo, "zep", "zep.py") == "def zep():\n    return 'zep'"
    assert _file_at(git_repo, "zep", "zep_helper.py") == (
        "def zep_helper():\n    return 'zep helper'"
    )
    assert "Sync finished successfully" in stdout.getvalue()


def test_sync_squash_preserves_code_changes_after_middle_branch_conflict_resolution(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.commit("shared base", filename="shared.py", content="VALUE = 'base'\n")
    git_repo.checkout_new("foo", from_ref="main")
    git_repo.commit(
        "foo api",
        filename="foo.py",
        content="def foo():\n    return 'foo'\n",
    )
    foo_fork = git_repo.merge_base("foo", "main")

    git_repo.checkout_new("bar", from_ref="foo")
    git_repo.commit(
        "bar edits shared",
        filename="shared.py",
        content="VALUE = 'bar'\n",
    )
    bar_fork = git_repo.merge_base("bar", "foo")

    git_repo.checkout_new("zep", from_ref="bar")
    git_repo.commit(
        "zep api",
        filename="zep.py",
        content="def zep():\n    return 'zep'\n",
    )
    zep_fork = git_repo.merge_base("zep", "bar")

    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="foo",
        parent_branch_name="main",
        fork_point_sha=foo_fork,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="bar",
        parent_branch_name="foo",
        fork_point_sha=bar_fork,
    )
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="zep",
        parent_branch_name="bar",
        fork_point_sha=zep_fork,
    )
    label_branch(stackman_db_path, repo_key, "foo", "stack-squash-conflict")

    git_repo.checkout("foo")
    git_repo.commit("foo edits shared", filename="shared.py", content="VALUE = 'foo'\n")

    def resolve_bar_conflict(_call_count: int) -> None:
        (git_repo.root / "shared.py").write_text("VALUE = 'foo + bar'\n")
        git_repo.git("add", "shared.py")
        subprocess.run(
            ["git", "-c", "core.editor=true", "rebase", "--continue"],
            cwd=git_repo.root,
            check=True,
            capture_output=True,
            text=True,
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=_ConflictResolverInput(resolve_bar_conflict),
        stdout=stdout,
        stderr=stderr,
    )
    assert app.sync(branch="foo", squash=True) == 0

    assert "Rebase failed on 'bar'" in stderr.getvalue()
    assert _file_at(git_repo, "foo", "foo.py") == "def foo():\n    return 'foo'"
    assert _file_at(git_repo, "foo", "shared.py") == "VALUE = 'foo'"

    assert _file_at(git_repo, "bar", "foo.py") == "def foo():\n    return 'foo'"
    assert _file_at(git_repo, "bar", "shared.py") == "VALUE = 'foo + bar'"

    assert _file_at(git_repo, "zep", "foo.py") == "def foo():\n    return 'foo'"
    assert _file_at(git_repo, "zep", "shared.py") == "VALUE = 'foo + bar'"
    assert _file_at(git_repo, "zep", "zep.py") == "def zep():\n    return 'zep'"


def test_sync_propagates_to_descendant_without_label(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("branch_a", from_ref="main")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout_new("branch_b", from_ref="branch_a")
    git_repo.commit("b", filename="b.txt", content="b\n")

    db_path = stackman_db_path
    initialize(db_path)
    fp_a = git_repo.merge_base("branch_a", "main")
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch_a",
        parent_branch_name="main",
        fork_point_sha=fp_a,
    )
    fp_b = git_repo.merge_base("branch_b", "branch_a")
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="branch_b",
        parent_branch_name="branch_a",
        fork_point_sha=fp_b,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "branch_a", "stack-x")

    git_repo.checkout("main")
    git_repo.commit("move main", filename="m.txt", content="m\n")

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert app.sync(branch="branch_a") == 0

    git_repo.checkout("branch_b")
    assert git_repo.is_ancestor(git_repo.rev_parse("main"), "HEAD")


def test_sync_implicit_stack_from_current_branch_labels(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f", filename="f.txt", content="f\n")
    fork = git_repo.merge_base("feature", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "only-stack")

    git_repo.checkout("main")
    git_repo.commit("m", filename="m2.txt", content="m\n")

    git_repo.checkout("feature")
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync() == 0
    assert "only-stack" not in stdout.getvalue()


def test_sync_runs_in_linked_worktree_when_branch_is_checked_out_there(
    git_repo,
    stackman_db_path,
    tmp_path: Path,
) -> None:
    git_repo.checkout_new("dead-code3", from_ref="main")
    git_repo.commit("feature work", filename="feat.txt", content="feat\n")
    fork = git_repo.merge_base("dead-code3", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="dead-code3",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "dead-code3", "sm_wt_stack")

    git_repo.checkout("main")
    wt = tmp_path / "dead-code3-wt"
    git_repo._run("worktree", "add", str(wt), "dead-code3")

    git_repo.commit("main moves", filename="main2.txt", content="main2\n")

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="dead-code3") == 0

    out = stdout.getvalue()
    assert str(wt.resolve()) in out
    assert "dead-code3" in out

    assert git_repo.current_branch() == "main"
    main_tip = git_repo.rev_parse("main")
    assert is_ancestor(wt, main_tip, "HEAD")


def test_sync_succeeds_when_unrelated_worktree_is_dirty(
    git_repo,
    stackman_db_path,
    tmp_path: Path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f", filename="f.txt", content="f\n")
    fork = git_repo.merge_base("feature", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-only")

    git_repo.checkout("main")
    noise = tmp_path / "noise-wt"
    git_repo.add_worktree(noise, new_branch="noise")
    (noise / "dirty.txt").write_text("noise\n")

    git_repo.commit("move main", filename="m3.txt", content="m3\n")

    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature") == 0


def test_sync_fails_with_details_when_involved_worktree_dirty(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f", filename="f.txt", content="f\n")
    fork = git_repo.merge_base("feature", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-x2")

    git_repo.checkout("main")
    (git_repo.root / "untracked-dirty.txt").write_text("x\n")

    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    assert app.sync(branch="feature") != 0
    err = stderr.getvalue()
    assert "untracked-dirty.txt" in err or "??" in err


def test_sync_allow_dirty_skips_dirty_preflight(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f", filename="f.txt", content="f\n")
    fork = git_repo.merge_base("feature", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-allow-dirty")

    git_repo.checkout("main")
    (git_repo.root / "untracked-dirty.txt").write_text("x\n")
    git_repo.commit("move main", filename="m4.txt", content="m4\n")

    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    assert app.sync(branch="feature", allow_dirty=True) == 0
    assert stderr.getvalue() == ""
    assert "--allow-dirty skips the dirty-worktree preflight" in stdout.getvalue()


def test_sync_allow_dirty_cannot_combine_with_squash(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f", filename="f.txt", content="f\n")
    fork = git_repo.merge_base("feature", "main")
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-dirty-squash")

    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    assert app.sync(branch="feature", allow_dirty=True, squash=True) != 0
    assert "cannot be combined" in stderr.getvalue()


def test_sync_waits_for_rebase_continue_and_then_resumes(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.commit("base shared", filename="shared.txt", content="base\n")
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature edits shared", filename="shared.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-conflict")

    git_repo.checkout("main")
    git_repo.commit("main edits shared", filename="shared.txt", content="main\n")
    parent_tip = git_repo.rev_parse("main")

    def resolve_rebase(call_count: int) -> None:
        if call_count == 1:
            return
        (git_repo.root / "shared.txt").write_text("main\nfeature\n")
        git_repo.git("add", "shared.txt")
        subprocess.run(
            ["git", "-c", "core.editor=true", "rebase", "--continue"],
            cwd=git_repo.root,
            check=True,
            capture_output=True,
            text=True,
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=_ConflictResolverInput(resolve_rebase),
        stdout=stdout,
        stderr=stderr,
    )
    assert app.sync(branch="feature") == 0
    err = stderr.getvalue()
    assert "Rebase failed on 'feature'" in err
    assert "was aborted" not in err

    tracked = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "feature")
    assert tracked is not None
    assert tracked.fork_point_sha == parent_tip
    out = stdout.getvalue()
    assert "press Enter to resume" in out
    assert "still in progress" in out
    assert "completed; resuming sync" in out


def test_sync_exits_non_zero_when_conflicted_rebase_is_aborted(
    git_repo,
    stackman_db_path,
) -> None:
    git_repo.commit("base shared", filename="shared.txt", content="base\n")
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature edits shared", filename="shared.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-abort")

    git_repo.checkout("main")
    git_repo.commit("main edits shared", filename="shared.txt", content="main\n")
    original_tip = git_repo.rev_parse("feature")
    original_fork = fork

    def abort_rebase(_call_count: int) -> None:
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=git_repo.root,
            check=True,
            capture_output=True,
            text=True,
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=_ConflictResolverInput(abort_rebase),
        stdout=stdout,
        stderr=stderr,
    )
    assert app.sync(branch="feature") != 0

    tracked = get_branch(stackman_db_path, git_repo.canonical_repo_key(), "feature")
    assert tracked is not None
    assert tracked.fork_point_sha == original_fork
    assert git_repo.rev_parse("feature") == original_tip
    assert "press Enter to resume" in stdout.getvalue()
    assert "was aborted" in stderr.getvalue()


def test_sync_pushes_after_squash_even_when_no_rebase_needed(
    git_repo,
    stackman_db_path,
    tmp_path,
) -> None:
    # Regression for the skip-guard/fork-point bug: when --squash rewrites HEAD but the
    # stored fork-point already matches the parent tip (no rebase needed), the branch
    # must still be pushed. Previously the `upstream == onto` guard skipped the push,
    # leaving the remote stale while reporting a skip.
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True
    )
    git_repo.git("remote", "add", "origin", str(remote))
    git_repo.git("push", "-u", "origin", "main")

    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f1", filename="f1.txt", content="one\n")
    git_repo.commit("f2", filename="f2.txt", content="two\n")
    git_repo.commit("f3", filename="f3.txt", content="three\n")
    git_repo.git("push", "-u", "origin", "feature")
    remote_before = git_repo.git("rev-parse", "origin/feature")

    # main does NOT move, so the stored fork-point equals main's tip: no rebase needed.
    fork = git_repo.merge_base("feature", "main")
    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(stackman_db_path, repo_key, "feature", "stack-1", anchor_branch_name="main")

    git_repo.checkout("main")
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature", squash=True) == 0

    out = stdout.getvalue()
    assert "collapsing 3 post-fork commits into one" in out
    assert "Pushing 'feature'" in out
    # The remote must now match the squashed local tip, not the stale pre-squash commit.
    local_tip = git_repo.rev_parse("feature")
    remote_after = git_repo.git("rev-parse", "origin/feature")
    assert remote_after == local_tip
    assert remote_after != remote_before
    assert _commit_count_in_range(git_repo, "main..feature") == 1


def test_sync_skips_push_when_remote_already_current(
    git_repo,
    stackman_db_path,
    tmp_path,
) -> None:
    remote = tmp_path / "remote2.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True, text=True
    )
    git_repo.git("remote", "add", "origin", str(remote))
    git_repo.git("push", "-u", "origin", "main")

    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f1", filename="f1.txt", content="one\n")
    git_repo.git("push", "-u", "origin", "feature")

    fork = git_repo.merge_base("feature", "main")
    initialize(stackman_db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        stackman_db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(stackman_db_path, repo_key, "feature", "stack-1", anchor_branch_name="main")

    git_repo.checkout("main")
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )
    assert app.sync(branch="feature") == 0
    assert "already up to date" in stdout.getvalue()


def test_sync_non_interactive_without_resolver_fails_with_conflict(
    git_repo,
    stackman_db_path,
) -> None:
    """Test that sync fails with a clear error when no resolver is provided and stdin is not interactive."""
    git_repo.commit("shared base", filename="shared.txt", content="base\n")
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature edits shared", filename="shared.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, git_repo.canonical_repo_key(), "feature", "stack-no-resolver")

    git_repo.checkout("main")
    git_repo.commit("main edits shared", filename="shared.txt", content="main\n")

    stdout = io.StringIO()
    stderr = io.StringIO()
    # Pass an empty StringIO stdin to simulate non-interactive mode
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    # Force non-interactive with no_wait=True and no resolver
    assert app.sync(branch="feature", no_wait=True) != 0

    err = stderr.getvalue()
    assert "Conflict resolution required" in err or "no resolver" in err.lower()


def test_sync_with_resolver_resolves_conflict(
    git_repo,
    stackman_db_path,
    tmp_path,
) -> None:
    """Test that sync with a resolver command succeeds when the resolver completes the rebase."""
    git_repo.commit("shared base", filename="shared.txt", content="base\n")
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature edits shared", filename="shared.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")

    db_path = stackman_db_path
    initialize(db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, repo_key, "feature", "stack-with-resolver")

    git_repo.checkout("main")
    git_repo.commit("main edits shared", filename="shared.txt", content="main\n")

    # Create a resolver script that resolves the conflict
    resolver_script = tmp_path / "resolver.sh"
    resolver_script.write_text(
        "#!/bin/bash\n"
        "# Simple resolver: take both sides\n"
        "git status --porcelain | grep -E '^UU|^AA|^DD' | awk '{print $2}' | while read file; do\n"
        "  echo 'main' > \"$file\"\n"
        "  echo 'feature' >> \"$file\"\n"
        '  git add "$file"\n'
        "done\n"
        "GIT_EDITOR=true git rebase --continue\n"
        "exit $?\n"
    )
    resolver_script.chmod(0o755)

    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    # Use --no-wait to force non-interactive mode and pass resolver
    result = app.sync(branch="feature", resolver=str(resolver_script), no_wait=True)
    out = stdout.getvalue()
    err = stderr.getvalue()
    if result != 0:
        print(f"Sync failed with code {result}")
        print(f"stdout: {out}")
        print(f"stderr: {err}")
    assert result == 0, f"Sync failed: {err}"

    assert "Invoking resolver" in out or "completed successfully" in out


def test_sync_with_failing_resolver_aborts_sync(
    git_repo,
    stackman_db_path,
    tmp_path,
) -> None:
    """Test that sync aborts when the resolver fails."""
    git_repo.commit("shared base", filename="shared.txt", content="base\n")
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("feature edits shared", filename="shared.txt", content="feature\n")
    fork = git_repo.merge_base("feature", "main")

    db_path = stackman_db_path
    initialize(db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha=fork,
    )
    label_branch(db_path, repo_key, "feature", "stack-failing-resolver")

    git_repo.checkout("main")
    git_repo.commit("main edits shared", filename="shared.txt", content="main\n")

    # Create a resolver script that fails
    resolver_script = tmp_path / "failing_resolver.sh"
    resolver_script.write_text("#!/bin/bash\n# Resolver that immediately fails\nexit 1\n")
    resolver_script.chmod(0o755)

    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    # Use --no-wait to force non-interactive mode and pass failing resolver
    result = app.sync(branch="feature", resolver=str(resolver_script), no_wait=True)
    assert result != 0

    err = stderr.getvalue()
    assert "failed" in err.lower() or "exit code" in err.lower()


def test_sync_multi_branch_stack_with_mid_stack_conflict_and_resolver(
    git_repo,
    stackman_db_path,
    tmp_path,
) -> None:
    """Integration test: multi-branch stack with conflict in the middle, resolved by resolver."""
    # Setup: main -> a -> b -> c
    git_repo.commit("base", filename="base.txt", content="base\n")

    # Branch a: clean
    git_repo.checkout_new("a", from_ref="main")
    git_repo.commit("a work", filename="a.txt", content="a\n")
    fork_a = git_repo.merge_base("a", "main")

    # Branch b: will cause conflict
    git_repo.checkout_new("b", from_ref="a")
    git_repo.commit("b edits shared", filename="shared.txt", content="b\n")
    fork_b = git_repo.merge_base("b", "a")

    # Branch c: clean
    git_repo.checkout_new("c", from_ref="b")
    git_repo.commit("c work", filename="c.txt", content="c\n")
    fork_c = git_repo.merge_base("c", "b")

    # Register the stack
    db_path = stackman_db_path
    initialize(db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        db_path,
        repo_root=repo_key,
        branch_name="a",
        parent_branch_name="main",
        fork_point_sha=fork_a,
    )
    upsert_branch(
        db_path, repo_root=repo_key, branch_name="b", parent_branch_name="a", fork_point_sha=fork_b
    )
    upsert_branch(
        db_path, repo_root=repo_key, branch_name="c", parent_branch_name="b", fork_point_sha=fork_c
    )
    label_branch(db_path, repo_key, "a", "stack-multi")

    # Cause conflict: main edits shared.txt
    git_repo.checkout("main")
    git_repo.commit("main edits shared", filename="shared.txt", content="main\n")

    # Create resolver that handles the conflict
    resolver_script = tmp_path / "resolver.sh"
    resolver_script.write_text(
        "#!/bin/bash\n"
        "# Resolver for multi-branch test\n"
        "for file in $(git status --porcelain | grep -E '^UU|^AA|^DD' | awk '{print $2}'); do\n"
        "  echo 'main' > \"$file\"\n"
        "  echo 'branch' >> \"$file\"\n"
        '  git add "$file"\n'
        "done\n"
        "GIT_EDITOR=true git rebase --continue\n"
        "exit $?\n"
    )
    resolver_script.chmod(0o755)

    # Run sync with resolver
    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    result = app.sync(branch="a", resolver=str(resolver_script), no_wait=True)
    assert result == 0, f"Sync failed: {stderr.getvalue()}"

    # Verify all branches were rebased and are ancestors of their parents
    git_repo.checkout("a")
    assert git_repo.is_ancestor(git_repo.rev_parse("main"), "HEAD")

    git_repo.checkout("b")
    assert git_repo.is_ancestor(git_repo.rev_parse("a"), "HEAD")

    git_repo.checkout("c")
    assert git_repo.is_ancestor(git_repo.rev_parse("b"), "HEAD")

    # Verify that fork-points were updated
    tracked_a = get_branch(db_path, repo_key, "a")
    assert tracked_a is not None
    assert tracked_a.fork_point_sha == git_repo.rev_parse("main")

    tracked_b = get_branch(db_path, repo_key, "b")
    assert tracked_b is not None
    assert tracked_b.fork_point_sha == git_repo.rev_parse("a")

    tracked_c = get_branch(db_path, repo_key, "c")
    assert tracked_c is not None
    assert tracked_c.fork_point_sha == git_repo.rev_parse("b")


def test_sync_detects_and_fixes_orphaned_fork_point(
    git_repo,
    stackman_db_path,
) -> None:
    """
    Test that stackman detects when a parent branch has been rebased,
    recalculates the fork-point, and syncs correctly.

    This reproduces the bug where:
    1. Parent branch (hot-eval) exists with commits
    2. Child branch (nick/lc-28816) is tracked with fork-point in parent
    3. Parent branch is rebased to new commits (old commits orphaned)
    4. Sync should detect orphaned fork-point and recalculate

    Regression test for: https://github.com/napisani/loancrate/pull/22338
    """
    # Setup: Create parent branch with initial commits
    git_repo.commit("base", filename="base.txt", content="base\n")

    git_repo.checkout_new("hot_eval_v1", from_ref="main")
    git_repo.commit("hot-eval v1 feature", filename="hoteval.txt", content="v1\n")

    # Create child branch based on parent's v1
    git_repo.checkout_new("feature", from_ref="hot_eval_v1")
    old_fork_point = git_repo.merge_base("feature", "hot_eval_v1")
    git_repo.commit("feature work 1", filename="feature.txt", content="feature 1\n")
    git_repo.commit("feature work 2", filename="feature2.txt", content="feature 2\n")
    git_repo.commit("feature work 3", filename="feature3.txt", content="feature 3\n")

    # Register feature branch with fork-point at old parent commit
    db_path = stackman_db_path
    initialize(db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        db_path,
        repo_root=repo_key,
        branch_name="feature",
        parent_branch_name="hot_eval_v1",
        fork_point_sha=old_fork_point,
    )
    label_branch(db_path, repo_key, "feature", "stack-test")

    # Verify initial state
    tracked = get_branch(db_path, repo_key, "feature")
    assert tracked is not None
    assert tracked.fork_point_sha == old_fork_point

    # NOW: Rebase the parent branch (this is what happened with hot-eval)
    # Reset hot_eval_v1 to a new set of commits
    git_repo.checkout("hot_eval_v1")
    # Reset to main (orphaning the old v1 commits)
    git_repo.git("reset", "--hard", "main")
    # Add new commits
    git_repo.commit("hot-eval v2 feature A", filename="hotevalA.txt", content="v2-A\n")
    git_repo.commit("hot-eval v2 feature B", filename="hotevalB.txt", content="v2-B\n")

    # Verify that old fork-point is no longer an ancestor of parent
    assert not git_repo.is_ancestor(old_fork_point, "hot_eval_v1")

    # Now sync the child branch
    # stackman should detect that old_fork_point is orphaned and recalculate
    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    assert app.sync(branch="feature") == 0
    assert stderr.getvalue() == ""

    out = stdout.getvalue()
    # Verify that stackman detected the orphaned fork-point
    assert (
        "Fork-point" in out
        or "orphaned" in out.lower()
        or "Recalculating" in out
        or "fork-point" in out.lower()
    )
    assert "Sync finished successfully" in out

    # Verify the feature branch is now correctly based on the new parent tip
    git_repo.checkout("feature")
    assert git_repo.is_ancestor(git_repo.rev_parse("hot_eval_v1"), "HEAD")

    # Verify that the fork-point was updated to the new parent tip
    tracked = get_branch(db_path, repo_key, "feature")
    assert tracked is not None
    # The new fork-point should be the merge-base of feature and hot_eval_v1 after rebase
    # which should be the new hot_eval_v1 tip (since feature was rebased onto it)
    expected_new_fork_point = git_repo.rev_parse("hot_eval_v1")
    assert tracked.fork_point_sha == expected_new_fork_point

    # Verify feature's commits are still there (rebased, not lost)
    # Note: after recalculating fork-point to main, feature will include all commits
    # since main (including the old parent commits A, and the 3 feature commits)
    commits = _commit_subjects_in_range(git_repo, "main..feature")
    assert len(commits) >= 3
    assert "feature work 1" in commits
    assert "feature work 2" in commits
    assert "feature work 3" in commits


def test_sync_handles_orphaned_fork_point_in_multi_level_stack(
    git_repo,
    stackman_db_path,
) -> None:
    """
    Test orphaned fork-point detection in a three-level stack (without conflicts).
    Verifies that when a parent is rebased, all descendants are correctly rebased.

    Stack: main -> parent -> child -> grandchild
    (Uses distinct files to avoid merge conflicts)
    """
    # Setup: Create a three-level stack
    git_repo.commit("base", filename="base.txt", content="base\n")

    # Parent level - edits parent-only.txt
    git_repo.checkout_new("parent", from_ref="main")
    git_repo.commit("parent v1", filename="parent-only.txt", content="parent-v1\n")
    parent_v1_fork = git_repo.merge_base("parent", "main")

    # Child level - edits child-only.txt (no conflict with parent)
    git_repo.checkout_new("child", from_ref="parent")
    git_repo.commit("child work", filename="child-only.txt", content="child\n")
    child_fork = git_repo.merge_base("child", "parent")

    # Grandchild level - edits grandchild-only.txt (no conflict with parent or child)
    git_repo.checkout_new("grandchild", from_ref="child")
    git_repo.commit("grandchild work", filename="grandchild-only.txt", content="grandchild\n")
    grandchild_fork = git_repo.merge_base("grandchild", "child")

    # Register the stack
    db_path = stackman_db_path
    initialize(db_path)
    repo_key = git_repo.canonical_repo_key()
    upsert_branch(
        db_path,
        repo_root=repo_key,
        branch_name="parent",
        parent_branch_name="main",
        fork_point_sha=parent_v1_fork,
    )
    upsert_branch(
        db_path,
        repo_root=repo_key,
        branch_name="child",
        parent_branch_name="parent",
        fork_point_sha=child_fork,
    )
    upsert_branch(
        db_path,
        repo_root=repo_key,
        branch_name="grandchild",
        parent_branch_name="child",
        fork_point_sha=grandchild_fork,
    )
    label_branch(db_path, repo_key, "parent", "stack-deep")

    # Rebase parent (orphaning old commits)
    git_repo.checkout("parent")
    git_repo.git("reset", "--hard", "main")
    git_repo.commit("parent v2", filename="parent-v2.txt", content="parent-v2\n")

    # Sync the entire stack
    stdout = io.StringIO()
    stderr = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=stderr,
    )
    result = app.sync(branch="parent")
    assert result == 0, f"Sync failed: stdout={stdout.getvalue()}, stderr={stderr.getvalue()}"
    assert stderr.getvalue() == ""

    # Verify all branches are correctly rebased
    git_repo.checkout("parent")
    assert git_repo.is_ancestor(git_repo.rev_parse("main"), "HEAD")

    git_repo.checkout("child")
    assert git_repo.is_ancestor(git_repo.rev_parse("parent"), "HEAD")

    git_repo.checkout("grandchild")
    assert git_repo.is_ancestor(git_repo.rev_parse("child"), "HEAD")

    # Verify fork-points were updated
    tracked_parent = get_branch(db_path, repo_key, "parent")
    assert tracked_parent is not None
    assert tracked_parent.fork_point_sha == git_repo.rev_parse("main")

    tracked_child = get_branch(db_path, repo_key, "child")
    assert tracked_child is not None
    assert tracked_child.fork_point_sha == git_repo.rev_parse("parent")

    tracked_grandchild = get_branch(db_path, repo_key, "grandchild")
    assert tracked_grandchild is not None
    assert tracked_grandchild.fork_point_sha == git_repo.rev_parse("child")
