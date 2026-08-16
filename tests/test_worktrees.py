from __future__ import annotations

import io
from pathlib import Path

from stackman.app import StackmanApp
from stackman.git_ops import repo_db_key
from stackman.store import initialize, list_branch_labels


def test_init_from_linked_worktree_inherits_parent_labels(
    git_repo,
    stackman_db_path,
    tmp_path: Path,
) -> None:
    git_repo.checkout_new("parent_line", from_ref="main")
    git_repo.commit("p", filename="p.txt", content="p\n")

    main_app = StackmanApp(
        db_path=stackman_db_path,
        cwd=git_repo.root,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        stack_id_factory=lambda: "sm_shared_wt",
    )
    git_repo.checkout("parent_line")
    assert main_app.track(parent="main") == 0

    wt = tmp_path / "linked-wt"
    git_repo.add_worktree(wt, new_branch="wt_tip")
    assert repo_db_key(wt) == git_repo.canonical_repo_key()

    wt_app = StackmanApp(
        db_path=stackman_db_path,
        cwd=wt,
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    assert wt_app.track(parent="parent_line") == 0

    initialize(stackman_db_path)
    key = git_repo.canonical_repo_key()
    assert list_branch_labels(stackman_db_path, key, "wt_tip") == ["sm_shared_wt"]
