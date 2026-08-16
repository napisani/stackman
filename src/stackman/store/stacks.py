from __future__ import annotations

from pathlib import Path

from ..models import StackRecord
from .branches import get_branch
from .connection import connect, normalize_path
from .rows import stack_from_row


def create_stack(
    db_path: Path | str,
    stack_id: str,
    *,
    anchor_branch_name: str | None = None,
) -> StackRecord:
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO stacks(id, anchor_branch_name)
            VALUES (?, ?)
            ON CONFLICT(id) DO UPDATE SET
                anchor_branch_name = COALESCE(stacks.anchor_branch_name, excluded.anchor_branch_name)
            """,
            (stack_id, anchor_branch_name),
        )
        row = conn.execute(
            "SELECT id, anchor_branch_name, created_at FROM stacks WHERE id = ?",
            (stack_id,),
        ).fetchone()
    return stack_from_row(row)


def get_stack(db_path: Path | str, stack_id: str) -> StackRecord | None:
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT id, anchor_branch_name, created_at FROM stacks WHERE id = ?",
            (stack_id,),
        ).fetchone()
    return stack_from_row(row) if row else None


def label_branch(
    db_path: Path | str,
    repo_root: Path | str,
    branch_name: str,
    stack_id: str,
    *,
    anchor_branch_name: str | None = None,
) -> None:
    branch = get_branch(db_path, repo_root, branch_name)
    if branch is None:
        raise LookupError(f"Unknown branch {branch_name!r} in repo {repo_root!s}")
    create_stack(db_path, stack_id, anchor_branch_name=anchor_branch_name)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO branch_stack_labels(branch_id, stack_id)
            VALUES (?, ?)
            ON CONFLICT(branch_id, stack_id) DO NOTHING
            """,
            (branch.id, stack_id),
        )


def clear_branch_labels(db_path: Path | str, repo_root: Path | str, branch_name: str) -> None:
    branch = get_branch(db_path, repo_root, branch_name)
    if branch is None:
        raise LookupError(f"Unknown branch {branch_name!r} in repo {repo_root!s}")
    with connect(db_path) as conn:
        conn.execute("DELETE FROM branch_stack_labels WHERE branch_id = ?", (branch.id,))


def list_branch_labels(db_path: Path | str, repo_root: Path | str, branch_name: str) -> list[str]:
    branch = get_branch(db_path, repo_root, branch_name)
    if branch is None:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT stack_id
            FROM branch_stack_labels
            WHERE branch_id = ?
            ORDER BY stack_id
            """,
            (branch.id,),
        ).fetchall()
    return [row[0] for row in rows]


def list_branch_names_with_stack_label(
    db_path: Path | str, repo_root: Path | str, stack_id: str
) -> list[str]:
    normalized = normalize_path(repo_root)
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT b.branch_name
            FROM branches AS b
            JOIN repos AS r ON r.id = b.repo_id
            JOIN branch_stack_labels AS l ON l.branch_id = b.id
            WHERE r.root_path = ? AND l.stack_id = ?
            ORDER BY b.branch_name
            """,
            (normalized, stack_id),
        ).fetchall()
    return [row[0] for row in rows]
