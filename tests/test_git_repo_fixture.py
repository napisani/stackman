from __future__ import annotations

import sys
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))

from git_repo_fixture import GitRepoFixture


def test_git_repo_fixture_creates_real_repository(tmp_path: Path) -> None:
    repo = GitRepoFixture.create(tmp_path / "repo")

    assert repo.current_branch() == "main"
    assert repo.branch_exists("main")
    assert repo.local_branches() == ["main"]
    assert len(repo.rev_parse("HEAD")) == 40


def test_git_repo_fixture_supports_real_branch_topology(tmp_path: Path) -> None:
    repo = GitRepoFixture.create(tmp_path / "repo")
    repo.checkout_new("branch_a", from_ref="main")
    sha_a = repo.commit("branch a commit", filename="a.txt", content="a\n")
    repo.checkout_new("branch_b", from_ref="branch_a")
    sha_b = repo.commit("branch b commit", filename="b.txt", content="b\n")

    assert repo.current_branch() == "branch_b"
    assert repo.branch_exists("branch_a")
    assert repo.branch_exists("branch_b")
    assert repo.is_ancestor("main", "branch_a")
    assert repo.is_ancestor("branch_a", "branch_b")
    assert repo.merge_base("branch_a", "branch_b") == sha_a
    assert repo.rev_parse("branch_b") == sha_b
