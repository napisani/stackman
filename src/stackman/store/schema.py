from __future__ import annotations

from pathlib import Path

from .connection import connect
from .repos import migrate_repo_roots_to_git_common_dir

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    root_path TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS stacks (
    id TEXT PRIMARY KEY,
    anchor_branch_name TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS branches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    branch_name TEXT NOT NULL,
    parent_branch_name TEXT,
    fork_point_sha TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, branch_name)
);

CREATE TABLE IF NOT EXISTS branch_stack_labels (
    branch_id INTEGER NOT NULL REFERENCES branches(id) ON DELETE CASCADE,
    stack_id TEXT NOT NULL REFERENCES stacks(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (branch_id, stack_id)
);

CREATE INDEX IF NOT EXISTS idx_branches_repo_parent
    ON branches(repo_id, parent_branch_name);
CREATE INDEX IF NOT EXISTS idx_branch_stack_labels_stack_id
    ON branch_stack_labels(stack_id);
CREATE INDEX IF NOT EXISTS idx_branches_repo_name
    ON branches(repo_id, branch_name);
"""


# Bump when adding a one-time data migration below; each new migration runs once
# per database and is then skipped on every subsequent command.
_SCHEMA_VERSION = 1


def initialize(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_stack_anchor_column(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            # Collapses worktree-specific repo paths to the git-common-dir key.
            # Spawns a git subprocess per repo, so it must not run on every command.
            migrate_repo_roots_to_git_common_dir(conn)
        if version < _SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _ensure_stack_anchor_column(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stacks)").fetchall()}
    if "anchor_branch_name" not in columns:
        conn.execute("ALTER TABLE stacks ADD COLUMN anchor_branch_name TEXT")
