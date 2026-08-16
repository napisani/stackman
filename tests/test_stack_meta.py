from __future__ import annotations

import io

from stackman.app import StackmanApp
from stackman.store import initialize, label_branch, upsert_branch


def test_list_shows_repo_local_tracked_branches(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("feature", from_ref="main")
    git_repo.commit("f", filename="f.txt", content="f\n")
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
    label_branch(db_path, repo_key, "feature", "sm_feature", anchor_branch_name="main")

    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.list() == 0
    out = stdout.getvalue()
    assert "Tracked branches in" in out
    assert "feature" in out
    # The tree roots at the (untracked) parent anchor with the branch nested under it.
    assert "main" in out
    assert "└── feature" in out
    assert "sm_feature" not in out


def test_list_shows_tracked_branches_without_stack_labels(git_repo, stackman_db_path) -> None:
    git_repo.checkout_new("dead-code2", from_ref="main")
    git_repo.commit("dc2", filename="dc2.txt", content="dc2\n")
    fork = git_repo.merge_base("dead-code2", "main")

    db_path = stackman_db_path
    initialize(db_path)
    upsert_branch(
        db_path,
        repo_root=git_repo.canonical_repo_key(),
        branch_name="dead-code2",
        parent_branch_name="main",
        fork_point_sha=fork,
    )

    stdout = io.StringIO()
    assert (
        StackmanApp(
            db_path=stackman_db_path,
            cwd=git_repo.root,
            stdin=io.StringIO(""),
            stdout=stdout,
            stderr=io.StringIO(),
        ).list()
        == 0
    )
    out = stdout.getvalue()
    assert "Tracked branches in" in out
    assert "dead-code2" in out
    assert "main" in out
    assert "└── dead-code2" in out
    assert "stacks" not in out


def test_list_empty_repo(git_repo, stackman_db_path) -> None:
    stdout = io.StringIO()
    app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert app.list() == 0
    assert "(none)" in stdout.getvalue()
