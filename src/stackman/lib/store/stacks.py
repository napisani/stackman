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
    """Set `branch_name`'s stack label to `stack_id`, replacing any previous one.

    A branch belongs to at most one stack at a time (see `HomelabApp`-style
    1:1 modeling note in schema.py's migration comment) — relabeling
    overwrites, it does not add to, the branch's stack membership.
    """
    branch = get_branch(db_path, repo_root, branch_name)
    if branch is None:
        raise LookupError(f"Unknown branch {branch_name!r} in repo {repo_root!s}")
    create_stack(db_path, stack_id, anchor_branch_name=anchor_branch_name)
    with connect(db_path) as conn:
        conn.execute(
            "UPDATE branches SET stack_id = ? WHERE id = ?",
            (stack_id, branch.id),
        )


def clear_branch_labels(db_path: Path | str, repo_root: Path | str, branch_name: str) -> None:
    branch = get_branch(db_path, repo_root, branch_name)
    if branch is None:
        raise LookupError(f"Unknown branch {branch_name!r} in repo {repo_root!s}")
    with connect(db_path) as conn:
        conn.execute("UPDATE branches SET stack_id = NULL WHERE id = ?", (branch.id,))


def list_branch_labels(db_path: Path | str, repo_root: Path | str, branch_name: str) -> list[str]:
    """A branch's stack label, as a 0- or 1-element list (kept list-shaped for
    call-site compatibility; a branch has at most one stack — see label_branch).
    """
    branch = get_branch(db_path, repo_root, branch_name)
    if branch is None or branch.stack_id is None:
        return []
    return [branch.stack_id]


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
            WHERE r.root_path = ? AND b.stack_id = ?
            ORDER BY b.branch_name
            """,
            (normalized, stack_id),
        ).fetchall()
    return [row[0] for row in rows]
