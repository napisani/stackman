from __future__ import annotations

import io

from stackman.app import StackmanApp
from stackman.store import get_branch, initialize, upsert_branch


def test_done_reparents_children_and_removes_branch(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("topic", from_ref="main")
    git_repo.commit("topic", filename="topic.txt", content="topic\n")
    git_repo.checkout_new("child_a", from_ref="topic")
    git_repo.commit("a", filename="a.txt", content="a\n")
    git_repo.checkout("topic")
    git_repo.checkout_new("child_b", from_ref="topic")
    git_repo.commit("b", filename="b.txt", content="b\n")

    key = git_repo.canonical_repo_key()
    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=key,
        branch_name="topic",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("topic", "main"),
    )
    upsert_branch(
        db_path,
        repo_root=key,
        branch_name="child_a",
        parent_branch_name="topic",
        fork_point_sha=git_repo.merge_base("child_a", "topic"),
    )
    upsert_branch(
        db_path,
        repo_root=key,
        branch_name="child_b",
        parent_branch_name="topic",
        fork_point_sha=git_repo.merge_base("child_b", "topic"),
    )

    git_repo.checkout("main")
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.done(branch="topic") == 0
    out = stdout.getvalue()
    assert "topic" in out and "main" in out
    assert get_branch(db_path, key, "topic") is None
    child_a = get_branch(db_path, key, "child_a")
    child_b = get_branch(db_path, key, "child_b")
    assert child_a is not None
    assert child_b is not None
    assert child_a.parent_branch_name == "main"
    assert child_b.parent_branch_name == "main"


def test_done_removes_branch_with_no_children(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("topic", from_ref="main")
    git_repo.commit("topic", filename="topic.txt", content="topic\n")
    git_repo.checkout("main")

    key = git_repo.canonical_repo_key()
    initialize(stackman_db_path)
    upsert_branch(
        stackman_db_path,
        repo_root=key,
        branch_name="topic",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("topic", "main"),
    )

    stdout = io.StringIO()
    assert (
        StackmanApp(
            db_path=stackman_db_path,
            cwd=git_repo.root,
            stdin=io.StringIO(""),
            stdout=stdout,
            stderr=io.StringIO(),
        ).done(branch="topic")
        == 0
    )
    assert get_branch(stackman_db_path, key, "topic") is None
    assert "removed it from stackman tracking" in stdout.getvalue()


def test_done_dry_run_does_not_mutate_db(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("topic", from_ref="main")
    git_repo.commit("topic", filename="topic.txt", content="topic\n")
    git_repo.checkout_new("child", from_ref="topic")
    git_repo.commit("child", filename="child.txt", content="child\n")

    key = git_repo.canonical_repo_key()
    initialize(stackman_db_path)
    upsert_branch(
        stackman_db_path,
        repo_root=key,
        branch_name="topic",
        parent_branch_name="main",
        fork_point_sha=git_repo.merge_base("topic", "main"),
    )
    upsert_branch(
        stackman_db_path,
        repo_root=key,
        branch_name="child",
        parent_branch_name="topic",
        fork_point_sha=git_repo.merge_base("child", "topic"),
    )

    stdout = io.StringIO()
    assert (
        StackmanApp(
            db_path=stackman_db_path,
            cwd=git_repo.root,
            stdin=io.StringIO(""),
            stdout=stdout,
            stderr=io.StringIO(),
        ).done(branch="topic", dry_run=True)
        == 0
    )
    assert get_branch(stackman_db_path, key, "topic") is not None
    child = get_branch(stackman_db_path, key, "child")
    assert child is not None
    assert child.parent_branch_name == "topic"
    assert "Dry run" in stdout.getvalue()
