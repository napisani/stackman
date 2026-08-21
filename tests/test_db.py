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

from stackman.lib.store import (
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
    # A branch belongs to at most one stack — relabeling replaces, not adds.
    assert list_branch_labels(db_path, repo_root, "feature") == ["stack-2"]

    label_branch(db_path, repo_root, "feature", "stack-1")
    assert list_branch_labels(db_path, repo_root, "feature") == ["stack-1"]


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


def test_migrating_v1_branch_stack_labels_join_table(tmp_path: Path) -> None:
    """A pre-existing v1 database used the branch_stack_labels M:N join table.
    initialize() must migrate it onto branches.stack_id (1:1), collapsing any
    branch that somehow had more than one label down to the alphabetically
    first one, and drop the old table.
    """
    import sqlite3

    from stackman.lib.store.connection import normalize_path

    db_path = tmp_path / "stackman.db"
    repo_root = tmp_path / "repo"

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                root_path TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE stacks (
                id TEXT PRIMARY KEY,
                anchor_branch_name TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
                branch_name TEXT NOT NULL,
                parent_branch_name TEXT,
                fork_point_sha TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(repo_id, branch_name)
            );
            CREATE TABLE branch_stack_labels (
                branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
                stack_id TEXT NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (branch_id, stack_id)
            );
            """
        )
        conn.execute("INSERT INTO repos(id, root_path) VALUES (1, ?)", (normalize_path(repo_root),))
        conn.execute("INSERT INTO stacks(id) VALUES ('stack-1'), ('stack-2'), ('stack-only')")
        conn.execute(
            "INSERT INTO branches(id, repo_id, branch_name, parent_branch_name, fork_point_sha) "
            "VALUES (1, 1, 'multi', 'main', 'abc1234'), (2, 1, 'single', 'main', 'def5678')"
        )
        # "multi" has two labels (a pre-existing corruption sync.py already
        # refused to operate on); "single" cleanly has just one.
        conn.execute(
            "INSERT INTO branch_stack_labels(branch_id, stack_id) VALUES "
            "(1, 'stack-2'), (1, 'stack-1'), (2, 'stack-only')"
        )
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
    finally:
        conn.close()

    initialize(db_path)

    assert list_branch_labels(db_path, repo_root, "multi") == ["stack-1"]
    assert list_branch_labels(db_path, repo_root, "single") == ["stack-only"]

    verify = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in verify.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "branch_stack_labels" not in tables
    finally:
        verify.close()


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
