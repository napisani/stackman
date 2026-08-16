from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, NewType

# Distinct string-shaped domain concepts. NewType is a zero-cost identity at runtime
# but lets a type checker catch a branch name passed where a stack id / repo key /
# SHA is expected (they were all bare `str` before).
BranchName = NewType("BranchName", str)
StackId = NewType("StackId", str)
RepoKey = NewType("RepoKey", str)
Sha = NewType("Sha", str)


@dataclass(frozen=True)
class RepoRecord:
    id: int
    root_path: str
    created_at: str | None = None


@dataclass(frozen=True)
class BranchRecord:
    id: int
    repo_id: int
    repo_root: str
    branch_name: BranchName
    parent_branch_name: BranchName | None
    fork_point_sha: Sha
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(frozen=True)
class StackRecord:
    id: StackId
    anchor_branch_name: BranchName | None = None
    created_at: str | None = None


@dataclass(frozen=True)
class ConflictResolutionResult:
    """Result of attempting to resolve a merge conflict during a rebase.

    Attributes:
        status: 'success' if rebase completed cleanly; 'failure' if resolver
                exited nonzero or rebase ended in an invalid state; 'needs_manual'
                if user aborted the rebase (interactive mode only).
        message: Human-readable summary of the result (e.g., error description,
                 or "Rebase completed successfully").
        resolver_output: Captured stdout/stderr from the resolver command,
                        if one was invoked (None for interactive mode).
    """

    status: Literal["success", "failure", "needs_manual"]
    message: str
    resolver_output: str | None = None
