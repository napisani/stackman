from __future__ import annotations

import sqlite3

from ..models import BranchName, BranchRecord, RepoRecord, Sha, StackId, StackRecord


def repo_from_row(row: sqlite3.Row | None) -> RepoRecord:
    if row is None:
        raise LookupError("Expected repo row")
    return RepoRecord(id=row["id"], root_path=row["root_path"], created_at=row["created_at"])


def branch_from_row(row: sqlite3.Row | None) -> BranchRecord:
    if row is None:
        raise LookupError("Expected branch row")
    parent = row["parent_branch_name"]
    stack_id = row["stack_id"]
    return BranchRecord(
        id=row["id"],
        repo_id=row["repo_id"],
        repo_root=row["root_path"],
        branch_name=BranchName(row["branch_name"]),
        parent_branch_name=BranchName(parent) if parent is not None else None,
        fork_point_sha=Sha(row["fork_point_sha"]),
        stack_id=StackId(stack_id) if stack_id is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def stack_from_row(row: sqlite3.Row | None) -> StackRecord:
    if row is None:
        raise LookupError("Expected stack row")
    anchor = row["anchor_branch_name"]
    return StackRecord(
        id=StackId(row["id"]),
        anchor_branch_name=BranchName(anchor) if anchor is not None else None,
        created_at=row["created_at"],
    )
