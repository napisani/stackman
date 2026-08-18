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
    stack_id TEXT REFERENCES stacks(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(repo_id, branch_name)
);

CREATE INDEX IF NOT EXISTS idx_branches_repo_parent
    ON branches(repo_id, parent_branch_name);
CREATE INDEX IF NOT EXISTS idx_branches_repo_name
    ON branches(repo_id, branch_name);
"""

# Indexes on columns that older databases only gain via an ALTER TABLE
# below (_ensure_*_column) — must run after those, not inside SCHEMA_SQL,
# since a pre-existing table's CREATE TABLE IF NOT EXISTS is a no-op and
# leaves the column missing until the ALTER TABLE runs.
_POST_COLUMN_MIGRATION_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_branches_stack_id
    ON branches(stack_id);
"""


# Bump when adding a one-time data migration below; each new migration runs once
# per database and is then skipped on every subsequent command.
_SCHEMA_VERSION = 2


def initialize(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA_SQL)
        _ensure_stack_anchor_column(conn)
        _ensure_branch_stack_id_column(conn)
        conn.executescript(_POST_COLUMN_MIGRATION_INDEX_SQL)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            # Collapses worktree-specific repo paths to the git-common-dir key.
            # Spawns a git subprocess per repo, so it must not run on every command.
            migrate_repo_roots_to_git_common_dir(conn)
        if version < 2:
            # branch_stack_labels was a branch<->stack M:N join table, but every
            # real caller only ever used it 1:1 (see commands/sync.py's
            # _resolve_stack_id, which already treated >1 label as unrecoverable
            # corruption). Collapse it onto a single stack_id column.
            _migrate_branch_labels_to_stack_id_column(conn)
        if version < _SCHEMA_VERSION:
            conn.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")


def _ensure_stack_anchor_column(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(stacks)").fetchall()}
    if "anchor_branch_name" not in columns:
        conn.execute("ALTER TABLE stacks ADD COLUMN anchor_branch_name TEXT")


def _ensure_branch_stack_id_column(conn) -> None:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(branches)").fetchall()}
    if "stack_id" not in columns:
        conn.execute(
            "ALTER TABLE branches ADD COLUMN stack_id TEXT REFERENCES stacks(id) ON DELETE SET NULL"
        )


def _migrate_branch_labels_to_stack_id_column(conn) -> None:
    """One-time migration off the old branch_stack_labels join table (M:N) onto
    branches.stack_id (1:1). A branch that somehow had more than one label
    keeps only its alphabetically-first one: sync.py already refused to
    operate on a multi-labeled branch (raising "ambiguous internal stack
    metadata" and telling the user to `forget` + re-track), so no branch
    reaching this migration with >1 label was in a usable state to begin with.
    """
    table_exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'branch_stack_labels'"
    ).fetchone()
    if table_exists is None:
        return
    rows = conn.execute(
        "SELECT branch_id, MIN(stack_id) AS stack_id FROM branch_stack_labels GROUP BY branch_id"
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE branches SET stack_id = ? WHERE id = ?",
            (row["stack_id"], row["branch_id"]),
        )
    conn.execute("DROP TABLE branch_stack_labels")
