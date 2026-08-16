from __future__ import annotations

import sys
import types
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if "stackman" not in sys.modules:
    package = types.ModuleType("stackman")
    package.__path__ = [str(SRC_DIR / "stackman")]
    sys.modules["stackman"] = package

from stackman.store import (
    create_stack,
    get_branch,
    get_stack,
    initialize,
    label_branch,
    list_branch_labels,
    list_branches,
    update_branch_fork_point,
    upsert_branch,
)


def test_db_initializes_and_persists_branch_records(tmp_path: Path) -> None:
    db_path = tmp_path / "stackman.db"
    initialize(db_path)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    branch = upsert_branch(
        db_path,
        repo_root=repo_root,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha="abc1234",
    )

    loaded = get_branch(db_path, repo_root, "feature")
    assert loaded == branch
    assert loaded is not None
    assert loaded.repo_root == str(repo_root.resolve())
    assert loaded.parent_branch_name == "main"
    assert loaded.fork_point_sha == "abc1234"


def test_db_supports_stack_labels(tmp_path: Path) -> None:
    db_path = tmp_path / "stackman.db"
    initialize(db_path)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    upsert_branch(
        db_path,
        repo_root=repo_root,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha="abc1234",
    )

    label_branch(db_path, repo_root, "feature", "stack-1")
    label_branch(db_path, repo_root, "feature", "stack-2")
    label_branch(db_path, repo_root, "feature", "stack-1")

    assert list_branch_labels(db_path, repo_root, "feature") == ["stack-1", "stack-2"]


def test_stack_anchor_is_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "stackman.db"
    initialize(db_path)

    created = create_stack(db_path, "stack-a", anchor_branch_name="release/1.2")
    loaded = get_stack(db_path, "stack-a")

    assert created.anchor_branch_name == "release/1.2"
    assert loaded is not None
    assert loaded.anchor_branch_name == "release/1.2"


def test_stack_anchor_is_filled_but_not_overwritten(tmp_path: Path) -> None:
    db_path = tmp_path / "stackman.db"
    initialize(db_path)

    create_stack(db_path, "stack-a")
    create_stack(db_path, "stack-a", anchor_branch_name="main")
    create_stack(db_path, "stack-a", anchor_branch_name="release/1.2")

    loaded = get_stack(db_path, "stack-a")
    assert loaded is not None
    assert loaded.anchor_branch_name == "main"


def test_db_list_branches_returns_normalized_records(tmp_path: Path) -> None:
    db_path = tmp_path / "stackman.db"
    initialize(db_path)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    upsert_branch(
        db_path,
        repo_root=repo_root,
        branch_name="b",
        parent_branch_name="a",
        fork_point_sha="deadbeef",
    )
    upsert_branch(
        db_path,
        repo_root=repo_root,
        branch_name="a",
        parent_branch_name="main",
        fork_point_sha="cafebabe",
    )

    branches = list_branches(db_path, repo_root)
    assert [branch.branch_name for branch in branches] == ["a", "b"]
    assert branches[0].repo_root == str(repo_root.resolve())


def test_db_updates_branch_fork_point(tmp_path: Path) -> None:
    db_path = tmp_path / "stackman.db"
    initialize(db_path)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    original = upsert_branch(
        db_path,
        repo_root=repo_root,
        branch_name="feature",
        parent_branch_name="main",
        fork_point_sha="abc1234",
    )

    updated = update_branch_fork_point(
        db_path,
        repo_root=repo_root,
        branch_name="feature",
        fork_point_sha="def5678",
    )

    assert updated is not None
    assert updated.id == original.id
    assert updated.repo_id == original.repo_id
    assert updated.repo_root == original.repo_root
    assert updated.branch_name == original.branch_name
    assert updated.parent_branch_name == original.parent_branch_name
    assert updated.fork_point_sha == "def5678"

    loaded = get_branch(db_path, repo_root, "feature")
    assert loaded == updated
