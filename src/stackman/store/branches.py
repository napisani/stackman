from __future__ import annotations

from pathlib import Path

from ..models import BranchRecord
from .connection import connect, normalize_path
from .repos import upsert_repo
from .rows import branch_from_row


def upsert_branch(
    db_path: Path | str,
    *,
    repo_root: Path | str,
    branch_name: str,
    parent_branch_name: str | None,
    fork_point_sha: str,
) -> BranchRecord:
    repo = upsert_repo(db_path, repo_root)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO branches(repo_id, branch_name, parent_branch_name, fork_point_sha)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(repo_id, branch_name) DO UPDATE SET
                parent_branch_name = excluded.parent_branch_name,
                fork_point_sha = excluded.fork_point_sha,
                updated_at = CURRENT_TIMESTAMP
            """,
            (repo.id, branch_name, parent_branch_name, fork_point_sha),
        )
        row = conn.execute(
            """
            SELECT b.id, b.repo_id, r.root_path, b.branch_name,
                   b.parent_branch_name, b.fork_point_sha, b.stack_id,
                   b.created_at, b.updated_at
            FROM branches AS b
            JOIN repos AS r ON r.id = b.repo_id
            WHERE r.root_path = ? AND b.branch_name = ?
            """,
            (repo.root_path, branch_name),
        ).fetchone()
    return branch_from_row(row)


def update_branch_fork_point(
    db_path: Path | str,
    *,
    repo_root: Path | str,
    branch_name: str,
    fork_point_sha: str,
) -> BranchRecord | None:
    normalized = normalize_path(repo_root)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE branches
            SET fork_point_sha = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = (
                SELECT b.id
                FROM branches AS b
                JOIN repos AS r ON r.id = b.repo_id
                WHERE r.root_path = ? AND b.branch_name = ?
            )
            """,
            (fork_point_sha, normalized, branch_name),
        )
        row = conn.execute(
            """
            SELECT b.id, b.repo_id, r.root_path, b.branch_name,
                   b.parent_branch_name, b.fork_point_sha, b.stack_id,
                   b.created_at, b.updated_at
            FROM branches AS b
            JOIN repos AS r ON r.id = b.repo_id
            WHERE r.root_path = ? AND b.branch_name = ?
            """,
            (normalized, branch_name),
        ).fetchone()
    return branch_from_row(row) if row else None


def get_branch(db_path: Path | str, repo_root: Path | str, branch_name: str) -> BranchRecord | None:
    normalized = normalize_path(repo_root)
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT b.id, b.repo_id, r.root_path, b.branch_name,
                   b.parent_branch_name, b.fork_point_sha, b.stack_id,
                   b.created_at, b.updated_at
            FROM branches AS b
            JOIN repos AS r ON r.id = b.repo_id
            WHERE r.root_path = ? AND b.branch_name = ?
            """,
            (normalized, branch_name),
        ).fetchone()
    return branch_from_row(row) if row else None


def list_branches(db_path: Path | str, repo_root: Path | str) -> list[BranchRecord]:
    normalized = normalize_path(repo_root)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT b.id, b.repo_id, r.root_path, b.branch_name,
                   b.parent_branch_name, b.fork_point_sha, b.stack_id,
                   b.created_at, b.updated_at
            FROM branches AS b
            JOIN repos AS r ON r.id = b.repo_id
            WHERE r.root_path = ?
            ORDER BY b.branch_name
            """,
            (normalized,),
        ).fetchall()
    return [branch_from_row(row) for row in rows]


def list_all_branches(db_path: Path | str) -> list[BranchRecord]:
    """Every tracked branch across all repos, ordered by repo then branch name."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT b.id, b.repo_id, r.root_path, b.branch_name,
                   b.parent_branch_name, b.fork_point_sha, b.stack_id,
                   b.created_at, b.updated_at
            FROM branches AS b
            JOIN repos AS r ON r.id = b.repo_id
            ORDER BY r.root_path, b.branch_name
            """
        ).fetchall()
    return [branch_from_row(row) for row in rows]


def list_branches_with_parent(
    db_path: Path | str, repo_root: Path | str, parent_branch_name: str
) -> list[BranchRecord]:
    normalized = normalize_path(repo_root)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT b.id, b.repo_id, r.root_path, b.branch_name,
                   b.parent_branch_name, b.fork_point_sha, b.stack_id,
                   b.created_at, b.updated_at
            FROM branches AS b
            JOIN repos AS r ON r.id = b.repo_id
            WHERE r.root_path = ? AND b.parent_branch_name = ?
            ORDER BY b.branch_name
            """,
            (normalized, parent_branch_name),
        ).fetchall()
    return [branch_from_row(row) for row in rows]


def delete_branch(db_path: Path | str, repo_root: Path | str, branch_name: str) -> bool:
    branch = get_branch(db_path, repo_root, branch_name)
    if branch is None:
        return False
    with connect(db_path) as conn:
        conn.execute("DELETE FROM branches WHERE id = ?", (branch.id,))
    return True


def reparent_children_and_delete_branch(
    db_path: Path | str,
    repo_root: Path | str,
    *,
    branch_name: str,
    reparents: list[tuple[str, str, str]],
) -> bool:
    """Atomically reparent children then delete a branch, in one transaction.

    ``reparents`` is a list of ``(child_branch, new_parent, fork_point_sha)``.
    Returns False (changing nothing) if the repo or branch row is absent.
    """
    normalized = normalize_path(repo_root)
    with connect(db_path) as conn:
        repo_row = conn.execute(
            "SELECT id FROM repos WHERE root_path = ?", (normalized,)
        ).fetchone()
        if repo_row is None:
            return False
        repo_id = repo_row["id"]
        for child, parent, fork in reparents:
            conn.execute(
                """
                INSERT INTO branches(repo_id, branch_name, parent_branch_name, fork_point_sha)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo_id, branch_name) DO UPDATE SET
                    parent_branch_name = excluded.parent_branch_name,
                    fork_point_sha = excluded.fork_point_sha,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (repo_id, child, parent, fork),
            )
        cursor = conn.execute(
            "DELETE FROM branches WHERE repo_id = ? AND branch_name = ?",
            (repo_id, branch_name),
        )
        return cursor.rowcount > 0


def delete_all_branches(db_path: Path | str, repo_root: Path | str) -> int:
    """Delete every tracked branch for one repo. Returns the number removed."""
    normalized = normalize_path(repo_root)
    with connect(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM branches
            WHERE repo_id = (SELECT id FROM repos WHERE root_path = ?)
            """,
            (normalized,),
        )
        removed = cursor.rowcount
        _delete_orphaned_stacks(conn)
    return removed


def delete_all_branches_global(db_path: Path | str) -> int:
    """Delete every tracked branch across all repos. Returns the number removed."""
    with connect(db_path) as conn:
        removed = conn.execute("DELETE FROM branches").rowcount
        conn.execute("DELETE FROM stacks")
    return removed


def _delete_orphaned_stacks(conn) -> None:
    """Drop stack rows that no branch references anymore."""
    conn.execute(
        "DELETE FROM stacks WHERE id NOT IN "
        "(SELECT stack_id FROM branches WHERE stack_id IS NOT NULL)"
    )
